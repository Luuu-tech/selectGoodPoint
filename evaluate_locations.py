import json
import math
import argparse
import csv
import os
from datetime import datetime

try:
    import folium
except ImportError:
    print("提示: 缺少 folium 库。请运行 'pip install folium' 以生成可视化地图。")
    folium = None

# ==========================================
# 1. 核心数学算法：地球球面距离计算 (Haversine公式)
# ==========================================
def calculate_distance(lat1, lon1, lat2, lon2):
    """计算两点经纬度之间的直线物理距离（单位：米）"""
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi / 2.0)**2 + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

# ==========================================
# 2. 核心业务逻辑：数据清洗与打分引擎
# ==========================================
def evaluate_candidates(data, effective_radius, half_decay_distance, power_weight, prop_weight):
    community_name = data.get("community_name", "未知小区")
    candidates = data.get("candidates", [])
    buildings_raw = data.get("buildings", [])

    # 数据清洗
    buildings = []
    for b in buildings_raw:
        if not b.get("lat") or not b.get("lng"):
            continue
        
        total_households = 0
        for u in b.get("units", []):
            hh = u.get("households", 0)
            if hh == "" or hh is None:
                hh = 0
            total_households += int(hh)
            
        buildings.append({
            "name": b.get("building_name", "未知楼栋"),
            "lat": float(b.get("lat")),
            "lng": float(b.get("lng")),
            "households": total_households
        })

    results = []

    for cand in candidates:
        cand_name = cand.get("name", "未命名点位")
        cand_lat = float(cand.get("lat"))
        cand_lng = float(cand.get("lng"))
        
        covered_households = 0
        raw_score = 0.0
        covered_buildings = []
        
        for b in buildings:
            dist = calculate_distance(cand_lat, cand_lng, b["lat"], b["lng"])
            
            if dist <= effective_radius:
                covered_households += b["households"]
                decay_factor = 1 / (1 + (dist / half_decay_distance)**2)
                effective_score = b["households"] * decay_factor
                raw_score += effective_score
                covered_buildings.append(f"{b['name']}({int(dist)}m)")

        # 自定义权重加成因子
        conditions = cand.get("conditions", {})
        multiplier = 1.0
        if conditions.get("has_power"): multiplier += power_weight
        if conditions.get("good_property"): multiplier += prop_weight

        final_score = raw_score * multiplier

        results.append({
            "候选点名称": cand_name,
            "经度": cand_lng,
            "纬度": cand_lat,
            f"{effective_radius}米内覆盖户数": covered_households,
            "原始衰减得分": round(raw_score, 2),
            "条件加成系数": f"{multiplier:.2f}x",
            "最终综合推荐分": round(final_score, 2),
            "辐射详情": " | ".join(covered_buildings)
        })

    results.sort(key=lambda x: x["最终综合推荐分"], reverse=True)
    return community_name, candidates, buildings, results

# ==========================================
# 3. 生成带打分标记的交互式地图
# ==========================================
def generate_html_map(community_name, candidates, buildings, results, radius, output_file):
    if not folium:
        return

    center_lat = buildings[0]["lat"] if buildings else candidates[0]["lat"]
    center_lng = buildings[0]["lng"] if buildings else candidates[0]["lng"]
    
    tiles_amap = "http://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}"
    m = folium.Map(location=[center_lat, center_lng], zoom_start=18, tiles=tiles_amap, attr='高德地图')

    # 绘制楼栋
    for b in buildings:
        folium.CircleMarker(
            location=[b["lat"], b["lng"]],
            radius=8 + (b["households"] / 20),
            popup=f"<b>{b['name']}</b><br>户数: {b['households']}",
            color="#3186cc",
            fill=True,
            fill_color="#3186cc"
        ).add_to(m)

    # 建立分数查找字典
    score_dict = {r["候选点名称"]: r["最终综合推荐分"] for r in results}

    # 绘制候选点及直观分数
    for c in candidates:
        c_name = c["name"]
        final_score = score_dict.get(c_name, "N/A")
        
        # 弹窗提示内展示分数
        popup_html = f"<b>预选点: {c_name}</b><br>测算得分: <b style='color:red; font-size:16px;'>{final_score}</b>"
        
        folium.Marker(
            location=[c["lat"], c["lng"]],
            popup=folium.Popup(popup_html, max_width=200),
            tooltip=f"{c_name} (得分: {final_score})", # 鼠标悬浮即显示分数
            icon=folium.Icon(color="red", icon="star")
        ).add_to(m)
        
        folium.Circle(
            location=[c["lat"], c["lng"]],
            radius=radius,
            color="red",
            weight=1,
            fill=True,
            fill_opacity=0.1
        ).add_to(m)

    m.save(output_file)
    print(f"✅ 交互式地图已生成: {output_file}")

# ==========================================
# 4. CLI 命令行入口
# ==========================================
def main():
    parser = argparse.ArgumentParser(description="地推网点选址空间分析引擎")
    parser.add_argument("input_json", help="前端导出的 JSON 数据文件路径")
    parser.add_argument("--radius", type=float, default=200, help="有效辐射半径(米)，默认 200")
    parser.add_argument("--decay", type=float, default=50, help="效能减半距离(米)，默认 50")
    parser.add_argument("--power", type=float, default=0.15, help="取电方便条件加分权重(0.15 即加成 15%)，默认 0.15")
    parser.add_argument("--prop", type=float, default=0.15, help="物业允入条件加分权重(0.15 即加成 15%)，默认 0.15")
    args = parser.parse_args()

    if not os.path.exists(args.input_json):
        print(f"❌ 错误: 找不到文件 {args.input_json}")
        return

    with open(args.input_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    community_name = data.get('community_name', '未知')
    print(f"🚀 开始分析小区数据: {community_name}...")
    
    community_name, candidates_raw, buildings_clean, report_data = evaluate_candidates(
        data, args.radius, args.decay, args.power, args.prop
    )

    if not report_data:
        print("⚠️ 未能计算出结果，请检查数据。")
        return

    # 动态获取当前日期做文件后缀
    date_suffix = datetime.now().strftime("%Y%m%d")

    csv_file = f"{community_name}_分析报告_{date_suffix}.csv"
    keys = report_data[0].keys()
    with open(csv_file, 'w', newline='', encoding='utf-8-sig') as f:
        dict_writer = csv.DictWriter(f, fieldnames=keys)
        dict_writer.writeheader()
        dict_writer.writerows(report_data)
    print(f"✅ 数据报表已导出: {csv_file}")

    html_file = f"{community_name}_选址可视化地图_{date_suffix}.html"
    generate_html_map(community_name, candidates_raw, buildings_clean, report_data, args.radius, html_file)

if __name__ == "__main__":
    main()