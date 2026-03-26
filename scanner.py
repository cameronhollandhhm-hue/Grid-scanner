import requests, math, time, logging
from datetime import datetime

TELEGRAM_BOT_TOKEN = "8785724347:AAFELFIZtKT1PSo5PLsg-4EHCBS_5IlLqLA"
TELEGRAM_CHAT_ID   = "6397743817"
SCAN_INTERVAL_MINS = 15
STABILITY_MIN      = 8

PRIMARY_ALERTS   = ["LONG GRID CANDIDATE", "SHORT GRID CANDIDATE", "GRID INVALIDATED"]
SECONDARY_ALERTS = []

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(), logging.FileHandler("scanner.log")])
log = logging.getLogger(__name__)

ASSETS  = [{"label":"BTC","symbol":"BTCUSDT"}, {"label":"ETH","symbol":"ETHUSDT"}]
BINANCE = "https://api.binance.com/api/v3"

def fetch_candles(symbol, interval="4h", limit=120):
    try:
        r = requests.get(f"{BINANCE}/klines",
            params={"symbol":symbol,"interval":interval,"limit":limit}, timeout=15)
        r.raise_for_status()
        return [{"o":float(c[1]),"h":float(c[2]),"l":float(c[3]),"c":float(c[4]),"v":float(c[5])}
                for c in r.json()]
    except Exception as e:
        log.error(f"fetch_candles({symbol}): {e}"); return None

def fetch_ticker(symbol):
    try:
        r = requests.get(f"{BINANCE}/ticker/24hr", params={"symbol":symbol}, timeout=15)
        r.raise_for_status(); d = r.json()
        return {"price":float(d["lastPrice"]),"change":float(d["priceChangePercent"])}
    except Exception as e:
        log.error(f"fetch_ticker({symbol}): {e}"); return None

def _avg(lst): return sum(lst)/len(lst) if lst else 0
def _tr(c,p): return max(c["h"]-c["l"], abs(c["h"]-p["c"]), abs(c["l"]-p["c"]))

def calc_atr(cs, p=14):
    trs=[cs[0]["h"]-cs[0]["l"]]+[_tr(cs[i],cs[i-1]) for i in range(1,len(cs))]
    a=_avg(trs[:p]); result=[None]*p
    for t in trs[p:]: a=(a*(p-1)+t)/p; result.append(a)
    return result

def calc_ema(data, p):
    k=2/(p+1); e=data[0]; result=[]
    for v in data: e=v*k+e*(1-k); result.append(e)
    return result

def chop_index(cs, p=14):
    sl=cs[-p:]; tr_sum=0
    for i,c in enumerate(sl):
        pv=sl[i-1]["c"] if i>0 else c["o"]
        tr_sum+=max(c["h"]-c["l"],abs(c["h"]-pv),abs(c["l"]-pv))
    hl=max(c["h"] for c in sl)-min(c["l"] for c in sl)
    return (math.log10(tr_sum/hl)/math.log10(p))*100 if hl else 50.0

def body_overlap(cs, n=10):
    sl=cs[-n:]; hits=0
    for i in range(1,len(sl)):
        p,c=sl[i-1],sl[i]
        if max(c["o"],c["c"])>min(p["o"],p["c"]) and min(c["o"],c["c"])<max(p["o"],p["c"]): hits+=1
    return hits/(len(sl)-1) if len(sl)>1 else 0

def swing_points(cs, n=3):
    lows=[]; highs=[]
    for i in range(n,len(cs)-n):
        c=cs[i]
        if all(c["l"]<cs[i-j-1]["l"] and c["l"]<cs[i+j+1]["l"] for j in range(n)): lows.append(c["l"])
        if all(c["h"]>cs[i-j-1]["h"] and c["h"]>cs[i+j+1]["h"] for j in range(n)): highs.append(c["h"])
    return lows, highs

def cluster(pts, tol):
    if not pts: return []
    sl=sorted(pts); cls=[[sl[0]]]
    for v in sl[1:]:
        a=_avg(cls[-1])
        if abs(v-a)<tol: cls[-1].append(v)
        else: cls.append([v])
    return sorted([{"level":_avg(c),"touches":len(c)} for c in cls], key=lambda x:-x["touches"])

def analyse(cs, price):
    N=len(cs); closes=[c["c"] for c in cs]
    atr_list=calc_atr(cs,min(14,N-1)); atr14=atr_list[-1] or price*0.02
    e20=calc_ema(closes,min(20,N)); e50=calc_ema(closes,min(50,N))
    W=min(12,max(4,N//4)); rec=cs[-W:]; prev=cs[-W*2:-W] or cs[-W:]
    rh=max(c["h"] for c in rec); rl=min(c["l"] for c in rec)
    move_pct=abs((rh-rl)/rl*100) if rl else 0
    atr_pct=atr14/price*100; threshold=2.5*atr_pct; has_move=move_pct>=threshold
    direction="impulse_up" if rec[-1]["c"]>rec[0]["o"] else "impulse_down"
    last4=cs[-4:]; prev8=cs[-12:-4] or last4
    body4=_avg([abs(c["c"]-c["o"]) for c in last4])
    body8=_avg([abs(c["c"]-c["o"]) for c in prev8]) or 1
    body_shrink=body4<body8*0.80
    ema20_slope=((e20[-1]-e20[-6])/e20[-6]*100) if len(e20)>6 else 0
    slope_flat=abs(ema20_slope)<0.75
    olap4=body_overlap(cs,min(4,N)); olap_inc=olap4>0.45
    slow_count=sum([body_shrink,olap_inc,slope_flat]); slowing=slow_count>=2
    momentum="slowing" if slowing else "neutral"
    if not has_move: pm_status="none"
    elif slowing:    pm_status=direction+"_complete"
    else:            pm_status=direction+"_ongoing"
    ci=chop_index(cs,min(14,N-1)); olap=body_overlap(cs,min(10,N))
    abs_slope=abs(ema20_slope)
    last10=cs[-min(10,N):]; dir_up=dir_dn=0
    for i in range(1,len(last10)):
        if last10[i]["h"]>last10[i-1]["h"] and last10[i]["l"]>last10[i-1]["l"]: dir_up+=1
        if last10[i]["h"]<last10[i-1]["h"] and last10[i]["l"]<last10[i-1]["l"]: dir_dn+=1
    strong_dir=max(dir_up,dir_dn)>7*((len(last10)-1)/10)
    if   ci>61  and olap>0.55 and abs_slope<=1.25:  state="sideways";     s_mkt=10
    elif ci>=52 and olap>=0.40 and abs_slope<=3.0:  state="weak_trend";   s_mkt=7
    elif ci<45  and olap<0.30 and strong_dir:        state="strong_trend"; s_mkt=0
    elif ci<52  and abs_slope>3.0:                   state="trending";     s_mkt=3
    else:                                             state="mixed";        s_mkt=5
    lb=cs[-min(72,N):]; sw=min(3,max(1,len(lb)//10))
    lows,highs=swing_points(lb,sw); tol=atr14*0.6
    sup_cl=cluster(lows,tol); res_cl=cluster(highs,tol)
    best_sup=next((s for s in sup_cl if s["level"]<price*0.999),None)
    best_res=next((r for r in res_cl if r["level"]>price*1.001),None)
    floor  =best_sup["level"] if best_sup else min(c["l"] for c in cs[-30:])
    ceiling=best_res["level"] if best_res else max(c["h"] for c in cs[-30:])
    mid=(floor+ceiling)/2; width=(ceiling-floor)/floor*100
    sup_t=best_sup["touches"] if best_sup else 0
    res_t=best_res["touches"] if best_res else 0
    last20=cs[-min(20,N):]
    inside=sum(1 for c in last20 if c["h"]<=ceiling*1.015 and c["l"]>=floor*0.985)
    inside_pct=inside/len(last20)
    if   sup_t>=2 and res_t>=2 and inside_pct>0.70: rng_q="clear"
    elif (sup_t>=1 or res_t>=1) and inside_pct>0.50: rng_q="developing"
    else:                                             rng_q="weak"
    pos=max(0.0,min(1.0,(price-floor)/(ceiling-floor)))
    pos_pct = round(pos*100, 0)
    if   pos_pct <= 20: pos_label = f"LOWER {int(pos_pct)}% (ideal for long)"
    elif pos_pct <= 40: pos_label = f"LOWER-MID {int(pos_pct)}% (acceptable)"
    elif pos_pct <= 60: pos_label = f"MIDDLE {int(pos_pct)}% (neutral)"
    elif pos_pct <= 80: pos_label = f"UPPER-MID {int(pos_pct)}% (avoid long)"
    else:               pos_label = f"TOP {int(pos_pct)}% (avoid long)"
    if   pos<0.20: zone="lower_third"
    elif pos<0.40: zone="lower_mid"
    elif pos<0.60: zone="middle"
    elif pos<0.80: zone="upper_mid"
    else:          zone="top"
    long_q ="ideal" if pos<=0.35 else ("acceptable" if pos<=0.45 else "poor")
    short_q="ideal" if pos>=0.65 else ("acceptable" if pos>=0.55 else "poor")
    stab_count=0
    for c in reversed(cs):
        if c["h"]<=ceiling*1.012 and c["l"]>=floor*0.988: stab_count+=1
        else: break
    stab_ok=stab_count>=STABILITY_MIN
    s_pm  =10 if pm_status.endswith("_complete") and slowing else (3 if "_ongoing" in pm_status else 0)
    s_rng ={"clear":10,"developing":6,"weak":2}[rng_q]
    s_el  ={"lower_third":10,"lower_mid":8,"middle":5,"upper_mid":2,"top":0}[zone]
    s_es  ={"top":10,"upper_mid":8,"middle":5,"lower_mid":2,"lower_third":0}[zone]
    stab_r=stab_count/STABILITY_MIN
    s_stab=10 if stab_r>=1 else (7 if stab_r>=0.7 else (4 if stab_r>=0.4 else 1))
    score_long =round(s_pm*.20+s_mkt*.25+s_rng*.25+s_el*.20+s_stab*.10,1)
    score_short=round(s_pm*.20+s_mkt*.25+s_rng*.25+s_es*.20+s_stab*.10,1)
    if   width>10 or rng_q=="developing": grid_style="WIDE (defensive)"
    elif width>=5:                          grid_style="MEDIUM (balanced)"
    else:                                   grid_style="TIGHT (aggressive)"
    inv_long =floor   - 0.5*atr14
    inv_short=ceiling + 0.5*atr14
    bad=state in("strong_trend","trending") or rng_q=="weak" or max(score_long,score_short)<5
    long_ok=(pm_status in("none","impulse_down_complete") and momentum!="expanding" and
             state in("sideways","weak_trend","mixed") and rng_q in("clear","developing") and
             long_q in("ideal","acceptable") and score_long>=6.5)
    short_ok=(pm_status in("none","impulse_up_complete") and momentum!="expanding" and
              state in("sideways","weak_trend","mixed") and
              short_q in("ideal","acceptable") and score_short>=6.5)
    if   bad:       verdict="NO TRADE"
    elif long_ok:   verdict="LONG GRID CANDIDATE"
    elif short_ok:  verdict="SHORT GRID CANDIDATE"
    elif max(score_long,score_short)>=5: verdict="WATCH — NOT READY"
    else:           verdict="NO TRADE"
    reasons = []
    if pm_status.endswith("_complete") and slowing: reasons.append("Dump completed")
    elif pm_status=="none": reasons.append("No prior impulse")
    else: reasons.append("Move still ongoing")
    if slowing: reasons.append("Momentum slowing")
    if sup_t>=2: reasons.append(f"Strong support tested {sup_t}x")
    elif sup_t==1: reasons.append("Support tested 1x (developing)")
    if state=="sideways": reasons.append("Market sideways")
    elif state=="weak_trend": reasons.append("Weak trend (acceptable)")
    else: reasons.append(f"Market {state}")
    return dict(verdict=verdict,score_long=score_long,score_short=score_short,
        state=state,ci=round(ci,1),olap=round(olap*100,0),
        pm_status=pm_status,move_pct=round(move_pct,1),threshold=round(threshold,1),
        momentum=momentum,slow_count=slow_count,floor=floor,ceiling=ceiling,mid=mid,
        width=round(width,1),rng_q=rng_q,sup_t=sup_t,res_t=res_t,
        inside_pct=round(inside_pct*100,0),pos=pos_pct,pos_label=pos_label,zone=zone,
        long_q=long_q,short_q=short_q,stab_count=stab_count,stab_ok=stab_ok,
        grid_style=grid_style,inv_long=inv_long,inv_short=inv_short,atr14=atr14,reasons=reasons)

def send_telegram(text):
    try:
        r=requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id":TELEGRAM_CHAT_ID,"text":text,"parse_mode":"Markdown",
                  "disable_web_page_preview":True}, timeout=15)
        if r.status_code==200: log.info("Telegram sent"); return True
        else: log.error(f"Telegram {r.status_code}: {r.text[:150]}"); return False
    except Exception as e:
        log.error(f"Telegram: {e}"); return False

def build_alert(label, price, change, a):
    chg_str = (f"+{change:.2f}%" if change>=0 else f"{change:.2f}%")
    score = max(a["score_long"], a["score_short"])
    score_label = "Ideal" if score>=8.5 else "Strong" if score>=7.5 else "Good" if score>=6.5 else "Average"
    ve = {"LONG GRID CANDIDATE":"🟢","SHORT GRID CANDIDATE":"🟣",
          "WATCH — NOT READY":"🟡","NO TRADE":"🔴","GRID INVALIDATED":"⚡"}.get(a["verdict"],"⚪")
    verdict_line = "GOOD SETUP — READY" if a["verdict"] in ("LONG GRID CANDIDATE","SHORT GRID CANDIDATE") else a["verdict"]
    reasons_text = "\n".join([f"— {r}" for r in a["reasons"]])
    entry_low  = a["floor"] + (a["ceiling"]-a["floor"])*0.05
    entry_high = a["floor"] + (a["ceiling"]-a["floor"])*0.20
    return "\n".join([
        f"{ve} *{label}/USDT — {a['verdict']}*",
        f"",
        f"Score: *{score} / 10* ({score_label})",
        f"",
        f"Range: *${a['floor']:,.0f} — ${a['ceiling']:,.0f}*",
        f"Position in range: *{a['pos_label']}*",
        f"",
        f"Grid Style: *{a['grid_style']}*",
        f"",
        f"Reason:",
        f"{reasons_text}",
        f"",
        f"Entry Zone: *${entry_low:,.0f} — ${entry_high:,.0f}*",
        f"Invalidation: Below *${a['inv_long']:,.0f}*",
        f"",
        f"Verdict: *{verdict_line}*",
        f"",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"Price: ${price:,.0f} ({chg_str})",
        f"Time: {datetime.now().strftime('%H:%M  %d/%m/%Y')}",
        f"_Not financial advice_",
    ])

def run_scan():
    log.info(f"Scan — {datetime.now().strftime('%H:%M:%S  %d/%m/%Y')}")
    for asset in ASSETS:
        label=asset["label"]; sym=asset["symbol"]
        cs=fetch_candles(sym,"4h",120); tk=fetch_ticker(sym)
        if cs is None or tk is None:
            log.error(f"{label}: fetch failed"); continue
        if len(cs)<20: continue
        price=tk["price"]; change=tk["change"]
        a=analyse(cs,price)
        log.info(f"{label}: ${price:,.0f} | {a['verdict']} | L:{a['score_long']} S:{a['score_short']} | Pos:{a['pos']}%")
        if a["verdict"] in PRIMARY_ALERTS:
            send_telegram(build_alert(label,price,change,a))
    log.info("Done.\n")

if __name__=="__main__":
    send_telegram(
        "🤖 *Grid Range Scanner v2 ONLINE*\n"
        "━━━━━━━━━━━━━━━━\n"
        "BTC & ETH · 4H candles\n"
        f"Every {SCAN_INTERVAL_MINS} minutes\n"
        "_Not financial advice_"
    )
    run_scan()
    while True:
        time.sleep(SCAN_INTERVAL_MINS*60)
        run_scan()
