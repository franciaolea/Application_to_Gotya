"""Inventory Management: stock vs minimum, automatic low-stock alerts, replenishment list.

Questions this page answers:
- Which products are below their minimum stock, and how bad is it?
- Which will sell out before a new delivery could arrive?
- What should we order, from whom, and what will it cost?
"""
import streamlit as st

from src import charts, config, metrics, ui

bundle = ui.get_data()
as_of = bundle.as_of
products = metrics.add_demand(metrics.enrich_inventory(bundle.frames["products"]),
                              bundle.frames["orders"], as_of)

st.title("Inventory Management")
st.caption(f"Stock position as of {as_of:%d %B %Y, %H:%M}. Minimum stock (reorder point) = average daily "
           f"demand × (supplier lead time + {config.SAFETY_STOCK_DAYS} safety days).")

# --- Filters ---------------------------------------------------------------
f1, f2, f3, f4 = st.columns(4)
with f1:
    categories = st.multiselect("Category", sorted(products["category"].unique()), placeholder="All")
with f2:
    warehouses = st.multiselect("Warehouse", sorted(products["warehouse"].unique()), placeholder="All")
with f3:
    suppliers = st.multiselect("Supplier", sorted(products["supplier"].unique()), placeholder="All")
with f4:
    statuses = st.multiselect("Stock status", ["Out of Stock", "Critical", "Low", "Healthy"], placeholder="All")
view = metrics.filter_frame(products, {"category": categories, "warehouse": warehouses,
                                       "supplier": suppliers, "stock_status": statuses})
if view.empty:
    st.info("No products match these filters.")
    st.stop()

issues = metrics.area_issues("Inventory", view)
below = view[view["needs_replenishment"]]
out_of_stock = view[view["stock_status"] == "Out of Stock"]
at_risk = view[view["stockout_risk"] & (view["stock_on_hand"] > 0)]

# --- KPIs ------------------------------------------------------------------
cards = st.columns(5)
with cards[0]:
    ui.kpi_card("Products", f"{len(view)}",
                f"{int(view['stock_on_hand'].sum()):,} units on hand<br>"
                f"{ui.fmt_eur(view['stock_value'].sum())} stock value at cost")
with cards[1]:
    counts = view["stock_status"].value_counts()
    ui.kpi_card("Below minimum", f"{len(below)}",
                f"{counts.get('Critical', 0)} critical · {counts.get('Low', 0)} low", metrics.status_of(issues))
with cards[2]:
    ui.kpi_card("Out of stock", f"{len(out_of_stock)}", "Losing sales until restocked",
                "Critical" if len(out_of_stock) else "On track")
with cards[3]:
    ui.kpi_card("Stock-out risk", f"{len(at_risk)}",
                "In stock now, but will sell out before a new order can arrive",
                "At risk" if len(at_risk) else "On track")
with cards[4]:
    ui.kpi_card("Replenishment cost", ui.fmt_eur(below["reorder_cost"].sum()),
                f"{len(below)} purchase lines suggested")

# --- Automatic low-stock alerts ----------------------------------------------
st.subheader("Low-stock alerts")
if below.empty:
    st.success("All products in this view are above their minimum stock.")
# Individual alerts only for what needs action today: out of stock, critical, or will sell out
# before a restock can arrive. Routine "low" items are summarised and listed in the table below.
urgent = below[(below["stock_status"].isin(["Out of Stock", "Critical"])) | below["stockout_risk"]]
for _, p in urgent.sort_values("days_of_cover").iterrows():
    days = round(p["days_of_cover"]) if p["daily_demand"] else None
    cover = "no sales in the last 4 weeks" if days is None else f"{days} day{'' if days == 1 else 's'} of cover"
    message = (f"**{p['product_name']}** ({p['sku']}): {p['stock_status'].lower()}, {int(p['stock_on_hand'])} in "
               f"stock vs minimum {int(p['min_stock'])} · {cover}, supplier lead time {int(p['lead_time_days'])} "
               f"days · reorder {int(p['suggested_order_qty'])} from {p['supplier']} · owner: {p['owner']}")
    if p["stock_status"] == "Out of Stock":
        st.error(message, icon="🔴")
    else:
        st.warning(message, icon="🟠")
routine = len(below) - len(urgent)
if routine:
    st.info(f"{routine} more product(s) are below minimum but will last until a restock arrives. "
            "They are included in the replenishment list below.")

# --- Replenishment list ------------------------------------------------------
st.subheader("Products requiring replenishment")
st.caption("Sorted by urgency (fewest days of cover first). Suggested quantity = the standard reorder "
           "quantity, or more if that would not reach the minimum.")
replenish = below.sort_values("days_of_cover")[[
    "sku", "product_name", "supplier", "stock_status", "stock_on_hand", "min_stock",
    "days_of_cover", "lead_time_days", "stockout_risk", "suggested_order_qty", "reorder_cost", "owner"]]
st.dataframe(
    replenish, hide_index=True, width="stretch",
    column_config={
        "sku": "SKU", "product_name": "Product", "supplier": "Supplier",
        "stock_status": "Status", "stock_on_hand": "In stock", "min_stock": "Minimum",
        "days_of_cover": st.column_config.NumberColumn("Days of cover", format="%.0f",
                                                       help="Stock ÷ average daily sales (last 4 weeks)"),
        "lead_time_days": st.column_config.NumberColumn("Lead time", format="%d days"),
        "stockout_risk": st.column_config.CheckboxColumn("Sells out before restock",
                                                         help="Days of cover shorter than the lead time"),
        "suggested_order_qty": "Order qty",
        "reorder_cost": st.column_config.NumberColumn("Est. cost", format="euro"),
        "owner": "Owner",
    })
if not replenish.empty:
    st.download_button("Download replenishment list (CSV)", replenish.to_csv(index=False),
                       file_name=f"replenishment_{as_of:%Y-%m-%d}.csv", mime="text/csv")

# --- Stock levels chart ------------------------------------------------------
with st.container(border=True):
    st.markdown("**Stock on hand vs minimum**")
    st.caption("Bars show stock on hand; the black tick marks each product's minimum. "
               "Products furthest below their minimum are at the top.")
    st.plotly_chart(charts.stock_vs_minimum(view), theme=None, config={"displayModeBar": False})

# --- Full inventory ------------------------------------------------------------
with st.expander(f"Full inventory ({len(view)} products)"):
    full = view.assign(coverage=(view["stock_on_hand"] / view["min_stock"] * 100).clip(upper=300))
    st.dataframe(
        full[["sku", "product_name", "category", "supplier", "warehouse", "stock_status", "stock_on_hand",
              "min_stock", "coverage", "units_sold", "days_of_cover", "lead_time_days", "unit_cost",
              "stock_value", "last_restock_date", "owner"]],
        hide_index=True, width="stretch",
        column_config={
            "coverage": st.column_config.ProgressColumn("Stock vs minimum", min_value=0, max_value=300,
                                                        format="%.0f%%", help="100% = exactly at minimum"),
            "units_sold": st.column_config.NumberColumn("Sold (4 wks)"),
            "days_of_cover": st.column_config.NumberColumn("Days of cover", format="%.0f"),
            "unit_cost": st.column_config.NumberColumn("Unit cost", format="euro"),
            "stock_value": st.column_config.NumberColumn("Stock value", format="euro"),
            "last_restock_date": st.column_config.DateColumn("Last restock", format="DD MMM YYYY"),
        })
