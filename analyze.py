"""
UK Online Retail Analytics — end-to-end data analysis.

Dataset: UCI Online Retail II (CC BY 4.0) — 1,067,371 transaction lines from a
UK-based online gift retailer, Dec 2009 – Dec 2011. One row = one product line
on one invoice. Columns: Invoice, StockCode, Description, Quantity, InvoiceDate,
Price, Customer ID, Country.

Pipeline: load -> clean -> engineer features -> answer business questions ->
build dashboard -> write findings.
"""
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

sns.set_theme(style="whitegrid")
plt.rcParams["figure.dpi"] = 110

RAW = "data/online_retail_II.xlsx"

# ---------------------------------------------------------------- load
print("Loading workbook...")
sheets = pd.read_excel(RAW, sheet_name=None, engine="openpyxl")
df = pd.concat(
    [s.assign(SourceSheet=name) for name, s in sheets.items()], ignore_index=True
)
df.columns = [c.strip() for c in df.columns]
print(f"Raw rows: {len(df):,} | columns: {list(df.columns)}")

# ---------------------------------------------------------------- clean
report = {"raw_rows": int(len(df))}
# 1) drop rows with no Customer ID (can't do customer-level analysis)
n_no_cust = int(df["Customer ID"].isna().sum())
df = df.dropna(subset=["Customer ID"]).copy()
df["Customer ID"] = df["Customer ID"].astype(int)
report["dropped_no_customer_id"] = n_no_cust

# 2) drop exact duplicates
n_dup = int(df.duplicated().sum())
df = df.drop_duplicates()
report["dropped_duplicates"] = n_dup

# 3) separate cancelled/return invoices (Invoice starts with 'C') and
#    non-positive quantities/prices from genuine sales
df["Invoice"] = df["Invoice"].astype(str)
is_cancel = df["Invoice"].str.startswith("C")
returns = df[is_cancel].copy()
sales = df[~is_cancel].copy()
report["return_rows"] = int(len(returns))

# keep only positive quantity & price in sales
bad = sales[(sales["Quantity"] <= 0) | (sales["Price"] <= 0)]
report["dropped_nonpositive_qty_price"] = int(len(bad))
sales = sales[(sales["Quantity"] > 0) & (sales["Price"] > 0)].copy()
report["clean_sales_rows"] = int(len(sales))

# ---------------------------------------------------------------- features
sales["Revenue"] = sales["Quantity"] * sales["Price"]
sales["InvoiceDate"] = pd.to_datetime(sales["InvoiceDate"])
sales["YearMonth"] = sales["InvoiceDate"].dt.to_period("M").astype(str)
sales["Year"] = sales["InvoiceDate"].dt.year
sales["Month"] = sales["InvoiceDate"].dt.month

orders = sales.groupby("Invoice").agg(
    revenue=("Revenue", "sum"),
    lines=("StockCode", "count"),
    country=("Country", "first"),
    date=("InvoiceDate", "min"),
    customer=("Customer ID", "first"),
)
report["n_orders"] = int(len(orders))
report["n_customers"] = int(sales["Customer ID"].nunique())
report["n_countries"] = int(sales["Country"].nunique())
report["date_min"] = str(sales["InvoiceDate"].min().date())
report["date_max"] = str(sales["InvoiceDate"].max().date())
report["total_revenue"] = round(float(sales["Revenue"].sum()), 2)
report["avg_order_value"] = round(float(orders["revenue"].mean()), 2)
report["median_order_value"] = round(float(orders["revenue"].median()), 2)

# monthly revenue
monthly = (
    sales.groupby("YearMonth")["Revenue"].sum().reset_index()
)
monthly["YearMonth"] = pd.to_datetime(monthly["YearMonth"])
monthly = monthly.sort_values("YearMonth")
report["best_month"] = monthly.loc[monthly["Revenue"].idxmax(), "YearMonth"].strftime("%Y-%m")
report["best_month_revenue"] = round(float(monthly["Revenue"].max()), 2)

# top products
top_products = (
    sales.groupby("Description")
    .agg(revenue=("Revenue", "sum"), qty=("Quantity", "sum"))
    .sort_values("revenue", ascending=False)
    .head(10)
)
report["top_products"] = [
    {"product": str(i), "revenue": round(float(r.revenue), 2), "qty": int(r.qty)}
    for i, r in top_products.iterrows()
]

# country concentration
country_rev = sales.groupby("Country")["Revenue"].sum().sort_values(ascending=False)
report["top_countries"] = [
    {"country": str(c), "revenue": round(float(v), 2)}
    for c, v in country_rev.head(8).items()
]
report["uk_share_pct"] = round(
    float(country_rev.get("United Kingdom", 0) / country_rev.sum() * 100), 1
)

# returns analysis
returns["Revenue"] = returns["Quantity"].abs() * returns["Price"]
report["return_rows_abs_revenue"] = round(float(returns["Revenue"].sum()), 2)
report["return_rate_by_rows_pct"] = round(len(returns) / (len(sales) + len(returns)) * 100, 2)

# ---------------------------------------------------------------- RFM
snapshot = sales["InvoiceDate"].max() + pd.Timedelta(days=1)
rfm = sales.groupby("Customer ID").agg(
    Recency=("InvoiceDate", lambda x: (snapshot - x.max()).days),
    Frequency=("Invoice", "nunique"),
    Monetary=("Revenue", "sum"),
)
# quintile scores: R reversed (lower recency = better)
rfm["R"] = pd.qcut(rfm["Recency"], 5, labels=[5, 4, 3, 2, 1], duplicates="drop").astype(int)
rfm["F"] = pd.qcut(rfm["Frequency"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5], duplicates="drop").astype(int)
rfm["M"] = pd.qcut(rfm["Monetary"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5], duplicates="drop").astype(int)
rfm["RFM_Score"] = rfm["R"] + rfm["F"] + rfm["M"]

def segment(row):
    s = row["RFM_Score"]
    if s >= 12:
        return "Champions"
    if s >= 10:
        return "Loyal"
    if s >= 8:
        return "Potential"
    if s >= 6:
        return "At Risk"
    return "Lost"

rfm["Segment"] = rfm.apply(segment, axis=1)
seg = rfm["Segment"].value_counts()
report["rfm_segments"] = {k: int(v) for k, v in seg.items()}
report["champions_revenue"] = round(float(rfm.loc[rfm["Segment"] == "Champions", "Monetary"].sum()), 2)

# ---------------------------------------------------------------- cohort retention
sales["CohortMonth"] = (
    sales.groupby("Customer ID")["InvoiceDate"].transform("min").dt.to_period("M")
)
sales["OrderMonth"] = sales["InvoiceDate"].dt.to_period("M")
cohort = (
    sales.groupby(["CohortMonth", "OrderMonth"])["Customer ID"]
    .nunique()
    .reset_index()
)
cohort["period"] = (cohort["OrderMonth"] - cohort["CohortMonth"]).apply(lambda x: x.n)
cohort_size = cohort[cohort["period"] == 0][["CohortMonth", "Customer ID"]].rename(
    columns={"Customer ID": "cohort_size"}
)
cohort = cohort.merge(cohort_size, on="CohortMonth")
cohort["retention"] = cohort["Customer ID"] / cohort["cohort_size"]
ret_pivot = cohort.pivot_table(
    index="CohortMonth", columns="period", values="retention"
)
# average retention for first 6 periods across cohorts with data
avg_ret = {}
for p in range(0, 7):
    if p in ret_pivot.columns:
        avg_ret[f"month_{p}"] = round(float(ret_pivot[p].mean()), 3)
report["avg_cohort_retention"] = avg_ret

# ---------------------------------------------------------------- dashboard
fig, axes = plt.subplots(2, 3, figsize=(20, 12))
fig.suptitle("UK Online Retail — Executive Dashboard (Dec 2009 – Dec 2011)", fontsize=16, weight="bold")

# 1. monthly revenue
ax = axes[0, 0]
ax.plot(monthly["YearMonth"], monthly["Revenue"], marker="o", ms=3, color="#1f77b4")
ax.set_title("Monthly Revenue")
ax.set_ylabel("Revenue (£)")
ax.tick_params(axis="x", rotation=45)
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"£{v/1e6:.1f}M"))

# 2. top products
ax = axes[0, 1]
tp = top_products.head(8).iloc[::-1]
ax.barh([str(i)[:38] for i in tp.index], tp["revenue"], color="#2ca02c")
ax.set_title("Top 8 Products by Revenue")
ax.set_xlabel("Revenue (£)")
ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"£{v/1e3:.0f}k"))

# 3. country share
ax = axes[0, 2]
top_c = country_rev.head(6)
others = country_rev.iloc[6:].sum()
vals = list(top_c.values) + [others]
labels = list(top_c.index) + ["Others"]
ax.pie(vals, labels=labels, autopct="%1.1f%%", startangle=90)
ax.set_title("Revenue Share by Country")

# 4. RFM segments
ax = axes[1, 0]
order = ["Champions", "Loyal", "Potential", "At Risk", "Lost"]
counts = [seg.get(s, 0) for s in order]
ax.bar(order, counts, color=["#2ca02c", "#1f77b4", "#ff7f0e", "#d62728", "#7f7f7f"])
ax.set_title("Customer Segments (RFM)")
ax.set_ylabel("Customers")
ax.tick_params(axis="x", rotation=25)

# 5. order value distribution
ax = axes[1, 1]
ax.hist(orders["revenue"].clip(upper=orders["revenue"].quantile(0.99)), bins=60, color="#9467bd")
ax.set_title("Order Value Distribution (clipped at 99th pct)")
ax.set_xlabel("Order value (£)")
ax.set_ylabel("Orders")

# 6. cohort retention heatmap (first 12 cohorts x 6 periods)
ax = axes[1, 2]
heat = ret_pivot.iloc[:12, :7]
sns.heatmap(heat, annot=True, fmt=".0%", cmap="YlGnBu", ax=ax, cbar=False)
ax.set_title("Cohort Retention (row = signup month)")
ax.set_xlabel("Months since first order")
ax.set_ylabel("Cohort")

plt.tight_layout(rect=[0, 0, 1, 0.96])
plt.savefig("dashboard.png", bbox_inches="tight")
print("Dashboard saved -> dashboard.png")

# ---------------------------------------------------------------- findings
with open("findings.json", "w") as f:
    json.dump(report, f, indent=2)
print("Findings saved -> findings.json")
print(json.dumps({k: v for k, v in report.items() if k not in ("top_products", "top_countries")}, indent=2)[:1500])
