#!/usr/bin/env python3
"""
七麦数据 - 杖剑传说 iOS游戏畅销榜排名爬虫 + 流水预测 + 可视化网页生成
App ID: 6473333072

功能:
  1. 抓取最近 7 天的 iOS 游戏畅销榜排名（无需登录）
  2. 更新 ranking_杖剑传说.csv
  3. 基于历史季度流水数据拟合幂律模型: daily_revenue = a * rank^b
  4. 生成可视化网页 report_杖剑传说.html

用法:
    python scrape_zhangjianchuanshuo.py
"""

import csv
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import requests
from scipy.optimize import minimize

# 修复 Windows GBK 编码下输出 emoji 报错的问题
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ======================== 配置 ========================
APP_ID = "6473333072"
APP_NAME = "杖剑传说"
COUNTRY = "cn"
GENRE_ID = "6014"   # iOS 游戏
BRAND = "grossing"  # 畅销榜

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.qimai.cn/",
    "Origin": "https://www.qimai.cn",
}

BASE_DIR = Path(__file__).parent
CSV_FILE = BASE_DIR / f"ranking_{APP_NAME}.csv"
REPORT_FILE = BASE_DIR / f"report_{APP_NAME}.html"
SCRAPE_DAYS = 7          # 只抓最近 7 天（无需登录）
REQUEST_DELAY = 1.5
MAX_PAGES = 6

# ======================== 季度流水（训练数据） ========================
QUARTERLY_REVENUE = [
    {"start": "2025-05-29", "end": "2025-06-30", "revenue": 4.24, "label": "2025 上线首月"},
    {"start": "2025-07-01", "end": "2025-09-30", "revenue": 7.42, "label": "2025 Q3"},
    {"start": "2025-10-01", "end": "2025-12-31", "revenue": 5.37, "label": "2025 Q4"},
    {"start": "2026-01-01", "end": "2026-03-31", "revenue": 5.48, "label": "2026 Q1"},
]

# 当前季度定义
CURRENT_QUARTER = {"start": "2026-04-01", "end": "2026-06-30", "label": "2026 Q2（当前）"}


def log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


# ==================== 日期工具 ====================

def parse_date(s: str) -> datetime:
    """兼容 YYYY-MM-DD 和 YYYY/M/D 两种日期格式"""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"无法解析日期: {s}")


def fmt_date(d: datetime) -> str:
    return d.strftime("%Y-%m-%d")


# ==================== CSV 读写 ====================

def load_csv() -> dict[str, int]:
    """读取 CSV，返回 {date_str: rank} 字典，date_str 统一为 YYYY-MM-DD"""
    data: dict[str, int] = {}
    if not CSV_FILE.exists():
        return data

    with open(CSV_FILE, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                d = fmt_date(parse_date(row["date"]))
                r = int(row["rank"])
                if r > 0:
                    data[d] = r
            except (ValueError, KeyError):
                continue
    return data


def save_csv(data: dict[str, int]) -> None:
    """写入 CSV，按日期排序"""
    sorted_items = sorted(data.items(), key=lambda x: x[0])
    with open(CSV_FILE, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "rank", "name"])
        for date_str, rank in sorted_items:
            writer.writerow([date_str, rank, APP_NAME])


# ==================== 七麦爬虫 ====================

def create_session() -> requests.Session:
    log("初始化 HTTP Session...")
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get("https://www.qimai.cn/", timeout=10)
    except Exception:
        pass
    return s


def fetch_daily_rank(session: requests.Session, date_str: str) -> int:
    """获取指定日期的排名，返回 >0=排名, 0=未上榜, -1=失败"""
    for page in range(1, MAX_PAGES + 1):
        url = (
            f"https://api.qimai.cn/rank/index"
            f"?brand={BRAND}&country={COUNTRY}&genre={GENRE_ID}"
            f"&date={date_str}&page={page}"
        )

        try:
            resp = session.get(url, timeout=20)
        except requests.RequestException as e:
            log(f"    ❌ 网络异常: {e}")
            return -1

        if resp.status_code != 200:
            log(f"    ⚠️ HTTP {resp.status_code}")
            return -1

        try:
            data = resp.json()
        except json.JSONDecodeError:
            return -1

        if data.get("code") != 10000:
            log(f"    ⚠️ API code={data.get('code')}, msg={data.get('msg', '')}")
            return -1

        items = data.get("rankInfo") or data.get("list") or []
        if isinstance(items, dict):
            items = items.get("list") or items.get("data") or []

        for item in items:
            app_id = item.get("app_id") or item.get("appInfo", {}).get("appId", "")
            if str(app_id) == APP_ID:
                return int(item.get("index", 0))

        if len(items) < 50:
            break

        time.sleep(0.4)

    return 0


def scrape_recent_days(session: requests.Session) -> dict[str, int]:
    """抓取最近 SCRAPE_DAYS 天的排名"""
    today = datetime.now()
    new_data: dict[str, int] = {}

    for i in range(SCRAPE_DAYS):
        d = today - timedelta(days=i)
        date_str = fmt_date(d)
        log(f"    抓取 {date_str} (page 1)...")
        rank = fetch_daily_rank(session, date_str)

        if rank > 0:
            log(f"    ✅ {date_str} → 第 {rank} 名")
            new_data[date_str] = rank
        elif rank == 0:
            log(f"    📭 {date_str} → 未上榜")
            new_data[date_str] = 0
        else:
            log(f"    ❌ {date_str} → 请求失败")

        if i < SCRAPE_DAYS - 1:
            time.sleep(REQUEST_DELAY)

    return new_data


# ==================== 模型拟合 ====================

def fit_model(rank_data: dict[str, int]) -> tuple[float, float]:
    """
    幂律模型: daily_revenue = a * rank^b
    用已知季度流水最小化 MSE 拟合参数 (a, b)
    """
    # 提取各季度对应的排名序列
    period_ranks = []
    for p in QUARTERLY_REVENUE:
        start = parse_date(p["start"])
        end = parse_date(p["end"])
        ranks = []
        current = start
        while current <= end:
            ds = fmt_date(current)
            r = rank_data.get(ds)
            if r and r > 0:
                ranks.append(r)
            current += timedelta(days=1)
        period_ranks.append(np.array(ranks, dtype=float))

    target = np.array([p["revenue"] for p in QUARTERLY_REVENUE])

    def objective(params):
        a, b = params
        preds = np.array([np.sum(a * (r ** b)) for r in period_ranks])
        return np.mean((preds - target) ** 2)

    result = minimize(objective, [1.0, -0.5], method="Nelder-Mead")
    a_opt, b_opt = result.x
    return float(a_opt), float(b_opt)


def compute_daily_revenue(rank: int, a: float, b: float) -> float:
    """单日流水预测 (亿元)"""
    if rank <= 0:
        return 0.0
    return a * (rank ** b)


# ==================== HTML 可视化 ====================

def generate_html(rank_data: dict[str, int], a: float, b: float) -> str:
    """生成完整的可视化网页"""

    # -------- 准备数据 --------
    sorted_dates = sorted(rank_data.keys())
    dates_for_js = [d for d in sorted_dates]
    ranks_for_js = [rank_data[d] for d in sorted_dates]
    revenues_for_js = [compute_daily_revenue(rank_data[d], a, b) for d in sorted_dates]

    # -------- 回测：各季度流水对比 --------
    backtest_rows = []
    all_periods = QUARTERLY_REVENUE + [CURRENT_QUARTER]
    backtest_json = []

    for p in all_periods:
        start = parse_date(p["start"])
        end = parse_date(p["end"])
        ranks_in_period = []
        current = start
        while current <= end:
            ds = fmt_date(current)
            r = rank_data.get(ds)
            if r and r > 0:
                ranks_in_period.append(r)
            current += timedelta(days=1)

        pred = sum(compute_daily_revenue(r, a, b) for r in ranks_in_period)
        actual = p.get("revenue")
        days_with_data = len(ranks_in_period)

        if actual is not None:
            error = (pred - actual) / actual * 100
            actual_str = f"{actual:.2f} 亿"
            error_str = f"{error:+.1f}%"
        else:
            actual_str = "（待季末公布）"
            error_str = "—"

        total_days = (end - start).days + 1
        is_current = p is CURRENT_QUARTER

        backtest_json.append({
            "label": p["label"],
            "actual": actual,
            "predicted": round(pred, 4),
            "days_data": days_with_data,
            "days_total": total_days,
            "is_current": is_current,
        })

    # -------- 当前季度信息 --------
    cq = backtest_json[-1]
    cq_predicted = cq["predicted"]
    cq_days_data = cq["days_data"]
    cq_days_total = cq["days_total"]
    cq_avg_daily = cq_predicted / cq_days_data if cq_days_data > 0 else 0
    cq_full_projection = cq_avg_daily * cq_days_total

    # -------- 最近30天 --------
    recent = sorted_dates[-30:]
    recent_table_rows = ""
    for d in reversed(recent):
        r = rank_data[d]
        rev = compute_daily_revenue(r, a, b)
        recent_table_rows += (
            f"<tr><td>{d}</td><td>{r}</td>"
            f"<td>{rev * 10000:.0f} 万</td></tr>\n"
        )

    # -------- 构建 HTML --------
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{APP_NAME} - 畅销榜排名与流水预测</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js">
</script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
       background: #f0f2f5; color: #333; min-height: 100vh; }}
.header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
          color: white; padding: 24px 32px; text-align: center; }}
.header h1 {{ font-size: 24px; margin-bottom: 6px; }}
.header .subtitle {{ font-size: 13px; opacity: 0.7; }}

/* 摘要卡片 */
.summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
           gap: 16px; padding: 24px 32px; max-width: 1200px; margin: 0 auto; }}
.card {{ background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.card .label {{ font-size: 12px; color: #888; margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }}
.card .value {{ font-size: 28px; font-weight: 700; color: #1a1a2e; }}
.card .value.highlight {{ color: #e74c3c; }}
.card .note {{ font-size: 11px; color: #aaa; margin-top: 4px; }}

/* 主体布局 */
.main {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; padding: 0 32px 24px; max-width: 1200px; margin: 0 auto; }}
@media (max-width: 800px) {{ .main {{ grid-template-columns: 1fr; }} }}
.section {{ background: white; border-radius: 10px; padding: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.section h3 {{ font-size: 15px; color: #1a1a2e; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #3498db; }}
.section.full {{ grid-column: 1 / -1; }}

/* 模型卡片 */
.model-info {{ display: flex; gap: 24px; flex-wrap: wrap; align-items: center; }}
.model-formula {{ font-size: 20px; font-family: 'Georgia', serif; color: #1a1a2e;
                 background: #f8f9fa; padding: 12px 20px; border-radius: 8px; white-space: nowrap; }}
.model-params {{ display: flex; gap: 16px; }}
.model-params .param {{ text-align: center; }}
.model-params .param .val {{ font-size: 22px; font-weight: 700; color: #3498db; }}
.model-params .param .lbl {{ font-size: 11px; color: #888; }}

/* 表格 */
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{ background: #f8f9fa; padding: 8px 10px; text-align: left; font-weight: 600; color: #555; border-bottom: 2px solid #ddd; }}
td {{ padding: 8px 10px; border-bottom: 1px solid #eee; }}
tr:hover td {{ background: #f8f9ff; }}
.positive {{ color: #27ae60; }}
.negative {{ color: #e74c3c; }}

/* 图表容器 */
.chart-wrap {{ position: relative; height: 280px; }}
.chart-wrap.tall {{ height: 360px; }}

/* 页脚 */
.footer {{ text-align: center; padding: 16px; color: #aaa; font-size: 11px; }}

/* 当前季度高亮行 */
tr.current-q {{ background: #fffdf0; }}
</style>
</head>
<body>

<div class="header">
  <h1>{APP_NAME} · 畅销榜排名与流水预测</h1>
  <div class="subtitle">模型: daily_revenue = a × rank<sup>b</sup> &nbsp;|&nbsp;
       a = {a:.4f}, b = {b:.4f} &nbsp;|&nbsp;
       数据更新于 {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
</div>

<div class="summary">
  <div class="card">
    <div class="label">当前季度 ({cq["label"]}) 预测流水</div>
    <div class="value highlight">{cq_predicted:.2f} 亿</div>
    <div class="note">基于 {cq_days_data}/{cq_days_total} 天排名数据累计</div>
  </div>
  <div class="card">
    <div class="label">全季度预估流水</div>
    <div class="value">{cq_full_projection:.2f} 亿</div>
    <div class="note">按日均流水 × {cq_days_total} 天线性推算</div>
  </div>
  <div class="card">
    <div class="label">日均预测流水（本季度）</div>
    <div class="value">{cq_avg_daily * 10000:.0f} 万</div>
    <div class="note">约 {cq_avg_daily:.4f} 亿元/天</div>
  </div>
  <div class="card">
    <div class="label">累计收录数据</div>
    <div class="value">{len(sorted_dates)} 天</div>
    <div class="note">{sorted_dates[0]} ~ {sorted_dates[-1]}</div>
  </div>
</div>

<div class="main">
  <!-- 模型信息 -->
  <div class="section">
    <h3>预测模型</h3>
    <div class="model-info">
      <div class="model-formula">日流水 = {a:.4f} × rank<sup>({b:.4f})</sup></div>
      <div class="model-params">
        <div class="param"><div class="val">{a:.4f}</div><div class="lbl">参数 a (系数)</div></div>
        <div class="param"><div class="val">{b:.4f}</div><div class="lbl">参数 b (指数)</div></div>
      </div>
    </div>
    <p style="margin-top:14px; font-size:12px; color:#888; line-height:1.6;">
      <strong>训练方式:</strong> 利用 4 个已知季度流水区间段 (2025/5~2026/3)，
      用 Nelder-Mead 算法最小化预测流水与实际流水的均方误差 (MSE)，
      拟合得到最优幂律参数。排名越靠前 (rank 越小)，单日流水越高。
    </p>
  </div>

  <!-- 回测表格 -->
  <div class="section">
    <h3>各季度流水回测对比</h3>
    <table>
      <thead><tr><th>季度</th><th>实际流水</th><th>模型预测</th><th>误差</th><th>数据天数</th></tr></thead>
      <tbody>
"""
    for bt in backtest_json:
        if bt["is_current"]:
            actual_display = "—"
            error_display = "—"
            row_class = " class='current-q'"
        else:
            actual_display = f"{bt['actual']:.2f} 亿"
            err = (bt["predicted"] - bt["actual"]) / bt["actual"] * 100
            cls = "positive" if abs(err) < 10 else "negative"
            error_display = f"<span class='{cls}'>{err:+.1f}%</span>"
            row_class = ""

        html += (
            f"<tr{row_class}><td>{bt['label']}</td>"
            f"<td>{actual_display}</td>"
            f"<td>{bt['predicted']:.2f} 亿</td>"
            f"<td>{error_display}</td>"
            f"<td>{bt['days_data']} 天</td></tr>\n"
        )

    html += """</tbody></table></div>

  <!-- 每日流水折线图 -->
  <div class="section full">
    <h3>每日预测流水趋势（亿元）</h3>
    <div class="chart-wrap tall"><canvas id="revenueChart"></canvas></div>
  </div>

  <!-- 排名趋势图 -->
  <div class="section full">
    <h3>每日畅销榜排名变化</h3>
    <div class="chart-wrap tall"><canvas id="rankChart"></canvas></div>
  </div>

  <!-- 回测柱状图 -->
  <div class="section full">
    <h3>季度流水: 实际 vs 预测</h3>
    <div class="chart-wrap"><canvas id="backtestChart"></canvas></div>
  </div>

  <!-- 最近30天数据表 -->
  <div class="section">
    <h3>最近 30 天排名与预测流水</h3>
    <table>
      <thead><tr><th>日期</th><th>排名</th><th>预测单日流水</th></tr></thead>
      <tbody>
""" + recent_table_rows + """</tbody></table>
  </div>

  <!-- 当前季度每日详情 -->
  <div class="section">
    <h3>当前季度每日流水预测明细</h3>
    <div style="max-height:400px; overflow-y:auto;">
    <table>
      <thead><tr><th>日期</th><th>排名</th><th>预测流水</th><th>累计 (亿)</th></tr></thead>
      <tbody>
"""
    # 当前季度每日数据
    cq_start = parse_date(CURRENT_QUARTER["start"])
    cq_end = parse_date(CURRENT_QUARTER["end"])
    cq_cumulative = 0.0
    current_d = cq_start
    while current_d <= cq_end:
        ds = fmt_date(current_d)
        r = rank_data.get(ds)
        if r and r > 0:
            rev = compute_daily_revenue(r, a, b)
            cq_cumulative += rev
            html += (f"<tr><td>{ds}</td><td>{r}</td>"
                     f"<td>{rev * 10000:.0f} 万</td>"
                     f"<td>{cq_cumulative:.4f} 亿</td></tr>\n")
        current_d += timedelta(days=1)

    html += """</tbody></table></div></div>
</div>

<div class="footer">
  @ 2026 雪球@月旨_投资笔记，仅供学习参考
</div>

<script>
// ============ 内嵌数据 ============
const dates = """ + json.dumps(dates_for_js) + """;
const ranks = """ + json.dumps(ranks_for_js) + """;
const revenues = """ + json.dumps([round(x, 6) for x in revenues_for_js]) + """;
const backtest = """ + json.dumps(backtest_json, ensure_ascii=False) + """;

// ============ Chart.js 全局配置 ============
Chart.defaults.font.family = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif";
Chart.defaults.font.size = 11;

// 季度分界竖线（标注用）
const quarterBoundaries = ['2025-07-01', '2025-10-01', '2026-01-01', '2026-04-01'];

// ============ 每日流水折线图 ============
(function() {
  const ctx = document.getElementById('revenueChart').getContext('2d');
  const colors = revenues.map(v => v > 0.02 ? '#e74c3c' : v > 0.01 ? '#f39c12' : '#3498db');
  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: dates,
      datasets: [{
        label: '预测日流水 (亿元)',
        data: revenues,
        backgroundColor: colors,
        borderColor: 'transparent',
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        tooltip: {
          callbacks: {
            label: ctx => '日流水: ' + ctx.raw.toFixed(4) + ' 亿元 (' + (ctx.raw * 10000).toFixed(0) + ' 万元)'
          }
        },
        legend: { display: false }
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 20, maxRotation: 45 },
          grid: { display: false }
        },
        y: {
          title: { display: true, text: '亿元/天' },
          ticks: { callback: v => v.toFixed(3) }
        }
      }
    }
  });
})();

// ============ 排名趋势图 ============
(function() {
  const ctx = document.getElementById('rankChart').getContext('2d');
  new Chart(ctx, {
    type: 'line',
    data: {
      labels: dates,
      datasets: [{
        label: '畅销榜排名',
        data: ranks,
        borderColor: '#3498db',
        backgroundColor: 'rgba(52, 152, 219, 0.1)',
        fill: true,
        pointRadius: 0,
        borderWidth: 1.5,
        tension: 0.1,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        tooltip: {
          callbacks: {
            label: ctx => '排名: 第 ' + ctx.raw + ' 名'
          }
        }
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 20, maxRotation: 45 },
          grid: { display: false }
        },
        y: {
          reverse: true,
          title: { display: true, text: '排名 (越小越好)' },
          ticks: { callback: v => '第 ' + v + ' 名' }
        }
      }
    }
  });
})();

// ============ 季度回测柱状图 ============
(function() {
  const ctx = document.getElementById('backtestChart').getContext('2d');
  const labels = backtest.filter(b => !b.is_current).map(b => b.label);
  const actuals = backtest.filter(b => !b.is_current).map(b => b.actual);
  const predicteds = backtest.filter(b => !b.is_current).map(b => b.predicted);

  new Chart(ctx, {
    type: 'bar',
    data: {
      labels: labels,
      datasets: [
        {
          label: '实际流水 (亿元)',
          data: actuals,
          backgroundColor: '#2c3e50',
          borderRadius: 4,
        },
        {
          label: '模型预测 (亿元)',
          data: predicteds,
          backgroundColor: '#3498db',
          borderRadius: 4,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        tooltip: { callbacks: { label: ctx => ctx.dataset.label + ': ' + ctx.raw.toFixed(2) + ' 亿' } }
      },
      scales: {
        x: { grid: { display: false } },
        y: { title: { display: true, text: '亿元' } }
      }
    }
  });
})();
</script>
</body>
</html>"""

    return html


# ==================== 主流程 ====================

def main():
    log("=" * 55)
    log(f"  {APP_NAME} · iOS游戏畅销榜排名 + 流水预测")
    log(f"  App ID: {APP_ID}")
    log("=" * 55)

    # ---------- Step 1: 读取现有 CSV ----------
    log("\n📂 Step 1: 读取现有排名数据...")
    rank_data = load_csv()
    log(f"    已加载 {len(rank_data)} 天的排名数据")

    # ---------- Step 2: 抓取最近 7 天 ----------
    log(f"\n🕷️ Step 2: 抓取最近 {SCRAPE_DAYS} 天排名（无需登录）...")
    session = create_session()
    new_data = scrape_recent_days(session)

    # 合并数据
    added = 0
    updated = 0
    for d, r in new_data.items():
        if d in rank_data:
            if rank_data[d] != r:
                updated += 1
        else:
            added += 1
        rank_data[d] = r

    if added or updated:
        log(f"\n💾 更新 CSV: 新增 {added} 天, 更新 {updated} 天")
    else:
        log(f"\n💾 数据无变化")
    save_csv(rank_data)

    # ---------- Step 3: 拟合模型 ----------
    log("\n📊 Step 3: 拟合幂律模型...")
    a_opt, b_opt = fit_model(rank_data)
    log(f"    最优参数: a = {a_opt:.4f}, b = {b_opt:.4f}")
    log(f"    模型公式: daily_revenue = {a_opt:.4f} × rank^({b_opt:.4f})")

    # 输出各季度回测
    log("\n    === 季度流水回测 ===")
    for p in QUARTERLY_REVENUE:
        start = parse_date(p["start"])
        end = parse_date(p["end"])
        ranks_in_p = []
        current = start
        while current <= end:
            ds = fmt_date(current)
            r = rank_data.get(ds)
            if r and r > 0:
                ranks_in_p.append(r)
            current += timedelta(days=1)
        pred = sum(compute_daily_revenue(r, a_opt, b_opt) for r in ranks_in_p)
        err = (pred - p["revenue"]) / p["revenue"] * 100
        log(f"    {p['label']}: 实际 {p['revenue']:.2f} 亿 | 预测 {pred:.2f} 亿 | 误差 {err:+.1f}%")

    # 当前季度
    cq_start = parse_date(CURRENT_QUARTER["start"])
    cq_end = parse_date(CURRENT_QUARTER["end"])
    cq_ranks = []
    current = cq_start
    while current <= cq_end:
        ds = fmt_date(current)
        r = rank_data.get(ds)
        if r and r > 0:
            cq_ranks.append(r)
        current += timedelta(days=1)
    cq_pred = sum(compute_daily_revenue(r, a_opt, b_opt) for r in cq_ranks)
    log(f"    {CURRENT_QUARTER['label']}: 预测累计 {cq_pred:.2f} 亿 ({len(cq_ranks)} 天)")

    # ---------- Step 4: 生成可视化网页 ----------
    log(f"\n📄 Step 4: 生成可视化网页...")
    html = generate_html(rank_data, a_opt, b_opt)
    REPORT_FILE.write_text(html, encoding="utf-8")
    log(f"    输出: {REPORT_FILE.resolve()}")
    log(f"    大小: {len(html):,} 字符")

    log("\n" + "=" * 55)
    log(f"  ✅ 全部完成!")
    log(f"  排名数据: {CSV_FILE.name} ({len(rank_data)} 天)")
    log(f"  可视化页: {REPORT_FILE.name}")
    log(f"  当前季度预测流水: {cq_pred:.2f} 亿元")
    log("=" * 55)


if __name__ == "__main__":
    main()
