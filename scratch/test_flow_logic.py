import sqlite3
from datetime import datetime

store_id = "ST1008"
conn = sqlite3.connect("C:/Users/Victus/.gemini/antigravity/worktrees/purple_tech/store-intelligence-pipeline-setup/store-intelligence/app/db/store_intelligence.db")
cursor = conn.cursor()

# Fetch all non-staff events
cursor.execute("""
    SELECT visitor_id, event_type, timestamp, zone_id
    FROM events
    WHERE store_id=? AND is_staff=0
    ORDER BY visitor_id, timestamp
""", (store_id,))
rows = cursor.fetchall()
print(f"Total query rows: {len(rows)}")

# Fetch POS transactions
cursor.execute("SELECT timestamp FROM pos_transactions WHERE store_id=?", (store_id,))
pos_times = [datetime.fromisoformat(row[0]) for row in cursor.fetchall()]
print(f"Total POS transactions: {len(pos_times)}")

visitor_events = {}
for visitor_id, event_type, timestamp, zone_id in rows:
    if visitor_id not in visitor_events:
        visitor_events[visitor_id] = []
    visitor_events[visitor_id].append({
        "event_type": event_type,
        "timestamp": datetime.fromisoformat(timestamp),
        "zone_id": zone_id
    })

print(f"Total visitors: {len(visitor_events)}")

links_count = {}
for visitor_id, evts in visitor_events.items():
    evts.sort(key=lambda x: x["timestamp"])
    path = ["Entry"]
    billing_enter_time = None
    has_abandon = False
    
    for evt in evts:
        e_type = evt["event_type"]
        zone = evt["zone_id"]
        
        if e_type == "BILLING_QUEUE_ABANDON":
            has_abandon = True
            
        if zone:
            zone_lower = zone.lower()
            category = None
            if "skincare" in zone_lower or "fragrance" in zone_lower:
                category = "Skincare"
            elif "makeup" in zone_lower or "mirror" in zone_lower or "gondola" in zone_lower or "table" in zone_lower:
                category = "Makeup"
            elif "billing" in zone_lower:
                category = "Billing"
                if not billing_enter_time:
                    billing_enter_time = evt["timestamp"]
            elif "aisle" in zone_lower or "center" in zone_lower:
                category = "Navigation"
            elif "summer" in zone_lower or "display" in zone_lower or "promo" in zone_lower:
                category = "Promo"
            else:
                category = zone.replace("_", " ").title()
                
            if category and category != path[-1]:
                path.append(category)
                
    if "Billing" in path:
        purchased = False
        if billing_enter_time:
            for txn_time in pos_times:
                diff_sec = (txn_time - billing_enter_time).total_seconds()
                if 0 <= diff_sec <= 300:
                    purchased = True
                    break
        if purchased and not has_abandon:
            pass
        else:
            path.append("Exit")
    else:
        path.append("Exit")
        
    for i in range(len(path) - 1):
        source = path[i]
        target = path[i+1]
        if source != target:
            link = (source, target)
            links_count[link] = links_count.get(link, 0) + 1

print(f"Links count: {links_count}")
conn.close()
