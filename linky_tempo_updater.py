import sys
sys.path.append('/config/deps')

import json
import requests
import websocket
from datetime import datetime, timedelta, timezone
import calendar
import os

# ==================== CONFIGURATION ====================
START_DATE_ORIGIN = datetime(2025, 8, 1, tzinfo=timezone.utc)
STATE_FILE = "/config/linky_tempo_state.json"

HA_WS_URL = "ws://homeassistant:8123/api/websocket"
HA_API_URL = "http://homeassistant:8123/api"
TOKEN = "TON_TOKEN"

SOURCE_STAT_ID = "linky:TON_PRM" 
CALENDAR_ID = "calendar.rte_tempo_calendrier"


# Tarifs tempo au 1 Aout 2025
PRIX = {
    'bleu_hc': 0.1232, 'bleu_hp': 0.1494, 
    'blanc_hc': 0.1391, 'blanc_hp': 0.1730,
    'rouge_hc': 0.1460, 'rouge_hp': 0.6468
}
PRIX_ABO_MOIS = 19.49

SENSORS = {
    'conso_bleu_hc':  ("linky_tempo:bleu_hc",  "input_number.linky_conso_bleu_hc"),
    'conso_bleu_hp':  ("linky_tempo:bleu_hp",  "input_number.linky_conso_bleu_hp"),
    'conso_blanc_hc': ("linky_tempo:blanc_hc", "input_number.linky_conso_blanc_hc"),
    'conso_blanc_hp': ("linky_tempo:blanc_hp", "input_number.linky_conso_blanc_hp"),
    'conso_rouge_hc': ("linky_tempo:rouge_hc", "input_number.linky_conso_rouge_hc"),
    'conso_rouge_hp': ("linky_tempo:rouge_hp", "input_number.linky_conso_rouge_hp"),
    'cout_bleu_hc':   ("linky_tempo:cout_bleu_hc", "input_number.linky_cout_bleu_hc"),
    'cout_bleu_hp':   ("linky_tempo:cout_bleu_hp", "input_number.linky_cout_bleu_hp"),
    'cout_blanc_hc':  ("linky_tempo:cout_blanc_hc", "input_number.linky_cout_blanc_hc"),
    'cout_blanc_hp':  ("linky_tempo:cout_blanc_hp", "input_number.linky_cout_blanc_hp"),
    'cout_rouge_hc':  ("linky_tempo:cout_rouge_hc", "input_number.linky_cout_rouge_hc"),
    'cout_rouge_hp':  ("linky_tempo:cout_rouge_hp", "input_number.linky_cout_rouge_hp"),
    'cout_abo':       ("linky_tempo:cout_abo",      "input_number.linky_cout_abo"),
    'conso_abo':      ("linky_tempo:conso_abo",     "input_number.linky_conso_abo"), 
}

# ==================== FONCTIONS ====================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
                last_date = datetime.fromisoformat(data['last_processed_date'])
                if last_date.tzinfo is None: last_date = last_date.replace(tzinfo=timezone.utc)
                # Migration auto pour ajouter conso_abo si manquant
                acc = data['accumulators']
                if 'conso_abo' not in acc: acc['conso_abo'] = 0.0
                return acc, last_date, set(data.get('processed_days_abo', []))
        except Exception: pass
    print("✨ Premier lancement : Initialisation au 01/08/2025.")
    return {k: 0.0 for k in SENSORS.keys()}, START_DATE_ORIGIN, set()

def save_state(accumulators, last_date, processed_days_abo):
    sorted_days = sorted(list(processed_days_abo))[-40:]
    data = {'accumulators': accumulators, 'last_processed_date': last_date.isoformat(), 'processed_days_abo': sorted_days}
    with open(STATE_FILE, 'w') as f: json.dump(data, f, indent=2)
    print(f"💾 État sauvegardé. Prochain scan : {last_date.isoformat()}")

def set_input_value(entity_id, value):
    try:
        requests.post(f"{HA_API_URL}/services/input_number/set_value", headers={"Authorization": f"Bearer {TOKEN}"}, json={"entity_id": entity_id, "value": round(value, 4)}, timeout=10)
    except Exception: pass

def get_tempo_colors(start_date, end_date):
    print("📅 Récupération Calendrier Tempo...")
    s_str = (start_date - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
    e_str = (end_date + timedelta(days=2)).strftime("%Y-%m-%dT23:59:59")
    try:
        r = requests.post(f"{HA_API_URL}/services/calendar/get_events?return_response=true", 
                          headers={"Authorization": f"Bearer {TOKEN}"}, 
                          json={"entity_id": CALENDAR_ID, "start_date_time": s_str, "end_date_time": e_str})
        data = r.json()
        response_root = data.get('service_response', {}) or data.get('response', {})
        if not isinstance(response_root, dict) or CALENDAR_ID not in response_root: return {}
        
        entity_data = response_root[CALENDAR_ID]
        if not isinstance(entity_data, dict): return {}
        
        events_raw = entity_data.get('events', [])
        events_list = list(events_raw.values()) if isinstance(events_raw, dict) else events_raw
        
        c_map = {}
        for ev in events_list:
            if not isinstance(ev, dict): continue 
            start_t = ev.get('start', {})
            date_str = None
            if isinstance(start_t, dict):
                date_str = start_t.get('date') or (start_t.get('dateTime', '').split('T')[0] if start_t.get('dateTime') else None)
            elif isinstance(start_t, str): date_str = start_t.split('T')[0]

            if date_str:
                txt = (str(ev.get('summary', '')) + ' ' + str(ev.get('description', ''))).lower()
                c_map[date_str] = 'blanc' if 'blanc' in txt or '⚪' in txt else 'rouge' if 'rouge' in txt or '🔴' in txt else 'bleu'
        print(f"   ✅ {len(c_map)} jours chargés.")
        return c_map
    except Exception: return {}

def get_context(dt_local, colors):
    h = dt_local.hour
    if h >= 6: ref_date = dt_local.date()
    else: ref_date = (dt_local - timedelta(days=1)).date()
    return colors.get(ref_date.isoformat(), 'bleu'), 'hp' if 6 <= h < 22 else 'hc'

def main():
    print("🚀 DÉMARRAGE LINKY TEMPO UPDATER")
    
    accumulators, last_processed_date, processed_days_abo = load_state()
    now_utc = datetime.now(timezone.utc)
    target_end_date = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    
    if last_processed_date >= target_end_date: print("✅ Tout est déjà à jour."); return

    try:
        ws = websocket.create_connection(HA_WS_URL)
        ws.recv(); ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        if json.loads(ws.recv()).get("type") != "auth_ok": return
    except Exception: return

    # Récupération Données
    all_stats = []
    curr = last_processed_date
    cmd_id = 101
    while curr < target_end_date:
        chunk_end = min(curr + timedelta(days=30), target_end_date)
        req = {
            "id": cmd_id, "type": "recorder/statistics_during_period",
            "start_time": curr.isoformat().replace("+00:00", "Z"), "end_time": chunk_end.isoformat().replace("+00:00", "Z"),
            "statistic_ids": [SOURCE_STAT_ID], "period": "hour"
        }
        ws.send(json.dumps(req))
        res = json.loads(ws.recv())
        if res.get("success"):
            batch = res.get("result", {}).get(SOURCE_STAT_ID, [])
            for pt in batch:
                ts = pt['start'] / 1000.0 if isinstance(pt['start'], (int, float)) else None
                if ts:
                    dt_pt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    if dt_pt > last_processed_date and dt_pt < target_end_date: all_stats.append(pt)
            print(f"   -> {len(batch)} points bruts.")
        curr = chunk_end; cmd_id += 1

    if not all_stats: print("⚠️ Pas de données."); ws.close(); return

    colors_map = get_tempo_colors(
        datetime.fromtimestamp(all_stats[0]['start']/1000, tz=timezone.utc),
        datetime.fromtimestamp(all_stats[-1]['start']/1000, tz=timezone.utc)
    )
    if not colors_map: print("⚠️ CALENDRIER HS."); ws.close(); return

    stats_buffers = {k: [] for k in SENSORS.keys()}
    try: import zoneinfo; tz_paris = zoneinfo.ZoneInfo("Europe/Paris")
    except: tz_paris = timezone(timedelta(hours=1))
        
    new_last_processed = last_processed_date
    print(f"⚙️ Calcul des tarifs pour {len(all_stats)} points (Conv Wh->kWh)...")

    for i, pt in enumerate(all_stats):
        ts_utc = datetime.fromtimestamp(pt['start']/1000, tz=timezone.utc)
        ts_local = ts_utc.astimezone(tz_paris)
        if ts_utc > new_last_processed: new_last_processed = ts_utc

        change_raw = 0.0
        if pt.get('change') is not None: 
            change_raw = float(pt['change'])
        elif i > 0: 
            change_raw = pt.get('sum', 0) - all_stats[i-1].get('sum', 0)
        
        # Division par 1000 car la source est en Wh
        change_kwh = change_raw / 1000.0
        
        if change_kwh < 0: change_kwh = 0
        if change_kwh > 20: change_kwh = 0 
        # -------------------------------------------

        iso_ts = ts_utc.isoformat().replace("+00:00", "Z")

        # Abonnement
        day_key = ts_local.strftime("%Y-%m-%d")
        if day_key not in processed_days_abo:
            days_in_month = calendar.monthrange(ts_local.year, ts_local.month)[1]
            accumulators['cout_abo'] += (PRIX_ABO_MOIS / days_in_month)
            stats_buffers['cout_abo'].append({"start_iso": iso_ts, "sum": accumulators['cout_abo']})
            stats_buffers['conso_abo'].append({"start_iso": iso_ts, "sum": 0.0})
            processed_days_abo.add(day_key)

        color, mode = get_context(ts_local, colors_map)
        k_conso, k_cout = f"conso_{color}_{mode}", f"cout_{color}_{mode}"
        
        accumulators[k_conso] += change_kwh
        accumulators[k_cout] += (change_kwh * PRIX.get(f"{color}_{mode}", 0))
        
        stats_buffers[k_conso].append({"start_iso": iso_ts, "sum": accumulators[k_conso]})
        stats_buffers[k_cout].append({"start_iso": iso_ts, "sum": accumulators[k_cout]})

    print("💾 Injection Base de données...")
    cmd_id = 500
    has_errors = False
    
    for k, (stat_id, _) in SENSORS.items():
        buffer = stats_buffers.get(k, [])
        source_domain = stat_id.split(':')[0]
        
        if not buffer:
            # Force création pour les jours rouges vides
            buffer = [{"start_iso": START_DATE_ORIGIN.isoformat().replace("+00:00", "Z"), "sum": 0.0, "state": 0.0}]

        for i in range(0, len(buffer), 1000):
            chunk = buffer[i:i+1000]
            stats_payload = [{"start": item["start_iso"], "sum": round(item["sum"], 4), "state": round(item.get("state", item["sum"]), 4)} for item in chunk]
            msg = {
                "id": cmd_id, "type": "recorder/import_statistics",
                "metadata": {
                    "statistic_id": stat_id, "source": source_domain,
                    "has_mean": False, "has_sum": True,
                    "unit_of_measurement": "EUR" if "cout" in k else "kWh",
                    "name": f"Linky Tempo {k.replace('_', ' ').title()}"
                },
                "stats": stats_payload
            }
            ws.send(json.dumps(msg))
            if not json.loads(ws.recv()).get("success"): has_errors = True
            cmd_id += 1

    print("📝 Mise à jour Input Numbers...")
    for k, (_, input_entity) in SENSORS.items():
        if input_entity: set_input_value(input_entity, accumulators[k])
            
    if not has_errors:
        save_state(accumulators, new_last_processed, processed_days_abo)
        print(f"✅ TERMINÉ SUCCÈS. Données jusqu'au {new_last_processed}")
    else: print("⚠️ ERREURS.")
    ws.close()

if __name__ == "__main__":
    main()