#!/usr/bin/env python3
"""앱에 넣은 빈자리 예측 모델(predictor.json)을 그대로 읽어, 모델을 만든 뒤에 쌓인 기록으로 채점합니다.

    .venv/bin/python ml/check_shipped.py      # → ml/reports/shipped.md, shipped.json

train_eval.py는 기록을 학습·시험으로 나눠 새로 학습하지만, 이 스크립트는 다시 학습하지 않아요.
모델 파일에 적힌 학습 기록 끝 시각(data.to) 뒤의 기록만 써서, 모델이 한 번도 보지 못한 기록에서 앱 모델이 얼마나 맞히는지 봐요.

  - 먼저 파이썬 채점기가 Kotlin 대조용 정답 사례(predictor_golden.json)와 같은 값을 내는지 확인해요.
  - 앱 안 조건: 패턴 입력은 앱 DB(assets/statio.db) 패턴표 한 칸에서 가져와요(export_model.device_features와 같은 규칙).
  - 참고: 패턴 입력을 학습 때 규칙(dataset.py, 기준 날짜 앞의 수집 기록)으로 만든 결과도 함께 적어요.
  - 기준선과 지표는 train_eval.py와 같아요.
"""

import json
import pathlib
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dataset as ds  # noqa: E402
import export_model as em  # noqa: E402
import train_eval as te  # noqa: E402

OUT = te.REPORT_DIR / "shipped.md"


# ── 모델 파일 채점기 (Kotlin SpacePredictor.leaf와 같은 규칙) ─────────────

def raw_score(ens, x):
    """노드 배열로 내보낸 앙상블의 원점수(잎 값 합 + baseline)를 샘플 전체에 한 번에 계산해요."""
    rows = np.arange(len(x))
    known = {int(f): np.isin(np.arange(256), cats) for f, cats in ens["known_categories"].items()}
    out = np.full(len(x), ens["baseline"])
    for t in ens["trees"]:
        feat, val = np.array(t["f"]), np.array(t["x"])
        left, right, miss_left = np.array(t["l"]), np.array(t["r"]), np.array(t["m"]).astype(bool)
        cat_left = {n: np.isin(np.arange(256), c) for n, c in zip(t["cat_nodes"], t["cat_left"])}
        node = np.zeros(len(x), dtype=int)
        while (inner := feat[node] >= 0).any():
            n, f = node[inner], feat[node[inner]]
            v = x[rows[inner], f]
            missing = np.where(miss_left[n], left[n], right[n])
            nxt = np.where(np.isnan(v), missing, np.where(v <= val[n], left[n], right[n]))
            for cn, goes_left in cat_left.items():
                sel = n == cn
                if not sel.any():
                    continue
                c = v[sel]
                ok = ~np.isnan(c) & (c >= 0)
                ci = np.where(ok, c, 0).astype(int).clip(0, 255)
                # 학습에 없던 범주는 값이 없는 것으로 봐요
                nxt[sel] = np.where(~ok, missing[sel], np.where(goes_left[ci], left[cn],
                                                               np.where(known[int(feat[cn])][ci], right[cn], missing[sel])))
            node[inner] = nxt
        out += val[node]
    return out


def predict(model, x):
    """(점유율 변화, 빈자리 있을 확률)이에요."""
    return raw_score(model["regressor"], x), 1 / (1 + np.exp(-raw_score(model["classifier"], x)))


def check_golden(model):
    cases = [c for c in json.loads(em.GOLDEN.read_text(encoding="utf-8")) if c.get("features")]
    x = np.array([c["features"] for c in cases], dtype=float)
    delta, prob = predict(model, x)
    ok = (np.abs(delta - [c["delta"] for c in cases]).max() < 1e-9) and (np.abs(prob - [c["prob_free"] for c in cases]).max() < 1e-9)
    if not ok:
        sys.exit("파이썬 채점기가 Kotlin 정답 사례와 다른 값을 내요. 모델 파일과 정답 사례를 export_model.py로 다시 만들어 주세요.")
    return len(cases)


# ── 앱 안 조건의 패턴 입력 ─────────────────────────────

def with_app_patterns(s):
    """패턴 입력 4개를 앱 DB 패턴표 한 칸 값으로 바꾼 사본이에요. 칸이 없으면 값 없음(NaN)이에요."""
    with sqlite3.connect(em.ASSET_DB) as conn:
        table = pd.DataFrame(conn.execute("SELECT lot_id, day_type, slot, free, days FROM patterns").fetchall(),
                             columns=["lot", "day_type", "slot", "p_free", "p_days"])
    out = s.copy()
    for which, slot, weekend in (("now", "slot_now", "weekend_now"), ("target", "slot_target", "weekend")):
        key = pd.DataFrame({"lot": s.lot.to_numpy(), "day_type": s[weekend].astype(int).to_numpy(), "slot": s[slot].to_numpy()})
        cell = key.merge(table, on=["lot", "day_type", "slot"], how="left")
        out[f"pat_{which}"] = (1 - cell.p_free / s.total.to_numpy()).to_numpy()
        if which == "target":
            out["pat_days"] = cell.p_days.fillna(0).to_numpy()
    out["pat_delta"] = out.pat_target - out.pat_now
    return out


# ── 리포트 ──────────────────────────────────────────────

def pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def write_report(info, metrics, full, days, worst_pattern):
    th = info["threshold"]
    labels = dict(te.LABELS, app_model="**앱 모델 (앱 안 조건)**", ref_model="참고: 앱 모델 (패턴 입력을 학습 규칙으로)")
    m_app, m_now, m_pat = metrics["app_model"]["all"][0], metrics["now"]["all"][0], metrics["pattern"]["all"][0]
    f_app, f_now = full["app"]["rule"], full["app"]["now_full"]
    L = ["# 앱 모델 사후 채점 리포트\n",
         f"> {info['made']}에 `ml/check_shipped.py`로 만들었어요. 앱에 넣은 모델 파일을 다시 학습하지 않고 그대로 읽어, "
         "**모델을 만든 뒤에 쌓인 기록**으로 채점했어요. 모델이 한 번도 보지 못한 기록이에요.",
         "\n## 대상과 기간",
         f"- 모델: `android/app/src/main/assets/predictor.json` ({info['model_made']}에 만듦, 학습 기록 {info['model_from']} ~ {info['model_to']})",
         f"- 채점 기간: {info['from']} ~ {info['to']} ({info['days_text']}), 샘플 {info['n']:,}개, 주차장 {info['lots']}곳(모델이 아는 {info['model_lots']}곳 중 값이 멈추지 않은 곳)",
         f"- 대조 확인: 파이썬 채점기가 Kotlin 대조용 정답 사례 {info['golden']}개와 같은 값을 냈어요(차이 1e-9 미만)",
         "- 앱 안 조건: 패턴 입력은 앱 DB 패턴표 한 칸에서, 최근 15·30분 변화는 수집 기록에서 가져왔어요(앱이 1분마다 쌓는 실시간 기록과 같은 규칙)",
         "\n## 만차 판단 (대표 지표)",
         f"채점 샘플 중 도착 때 만차는 {full['app']['full_rows']:,}개예요(지금도 만차 {full['app']['full_rows'] - full['app']['trans_rows']:,}개, "
         f"도착 전에 차는 전환 {full['app']['trans_rows']:,}개). 기준값은 모델 파일에 들어 있는 {th:.2f}을 그대로 썼어요.\n",
         "| 방법 | precision | recall (만차 포착) | F1 | **F2** | 전환 포착 | 헛경보 |",
         "|---|---|---|---|---|---|---|"]
    for label, r in (("지금 만차만 보기", full["app"]["now_full"]), ("**앱 모델 (앱 안 조건)**", full["app"]["rule"]),
                     ("참고: 앱 모델 (패턴 입력을 학습 규칙으로)", full["ref"]["rule"])):
        L.append(f"| {label} | {pct(r['precision'])} | {pct(r['recall'])} | {r['f1']:.3f} | **{r['f2']:.3f}** | {pct(r['trans'])} | {pct(r['false_alarm'])} |")
    L += ["\n## 도착 시 빈자리 평균 오차 (면)",
          "| 방법 | 10분 뒤 | 20분 뒤 | 30분 뒤 | 5~60분 전체 | 적중률 |",
          "|---|---|---|---|---|---|"]
    for k in ("now", "pattern", "app_model", "ref_model"):
        m = metrics[k]
        hit = "—" if m["hit_all"] is None else f"{m['hit_all']:.1f}%"
        L.append(f"| {labels[k]} | {te.fmt(m['h10'])} | {te.fmt(m['h20'])} | {te.fmt(m['h30'])} | {te.fmt(m['all'])} | {hit} |")
    L.append("\n적중률은 오차가 '2면 또는 총 주차면의 5% 중 큰 값' 안에 든 비율이에요. 기준선 ② 패턴표는 학습 규칙(기준 날짜 앞 28일, 3일 이상)으로 "
             f"계산할 수 있는 샘플({metrics['pattern']['all'][1]:,}개)에서만 쟀어요.")
    L += ["\n## 날짜별",
          "| 날짜 | 샘플 | 도착 때 만차 | 앱 모델 오차 | 지금 값 유지 오차 | 앱 모델 F2 | 지금 만차만 F2 |",
          "|---|---|---|---|---|---|---|"]
    for d in days:
        f2 = lambda r: "—" if r["recall"] is None or r["precision"] is None else f"{r['f2']:.3f}"  # noqa: E731
        L.append(f"| {d['date']} | {d['n']:,} | {d['full']:,} | {d['app']:.2f} | {d['now']:.2f} | {f2(d['rule'])} | {f2(d['now_full'])} |")
    L += ["\n## 패턴표 오차가 큰 주차장",
          "기준선 ② 패턴표는 지난 날짜로 만들어서, 평소와 다른 날에는 크게 빗나가요. 오차 합에서 차지하는 몫이 큰 곳이에요.\n",
          "| 주차장 | 총 주차면 | 패턴표 오차 | 지금 값 유지 오차 | 앱 모델 오차 | 패턴표 오차 몫 |",
          "|---|---|---|---|---|---|"]
    for r in worst_pattern:
        L.append(f"| {r['name']} | {r['total']:,} | {r['pattern']:.1f} | {r['now']:.1f} | {r['app']:.1f} | {r['share']:.0f}% |")
    L += ["\n## 해석",
          f"- 만차 판단: 앱 모델은 F2 {f_app['f2']:.3f}(지금 만차만 보면 {f_now['f2']:.3f})이고, 도착 때 만차를 {f_app['recall'] * 100:.1f}% 잡아요"
          f"(지금 만차만 보면 {f_now['recall'] * 100:.1f}%). 경고 중 실제 만차는 {f_app['precision'] * 100:.1f}%예요.",
          f"- 빈자리 수: 5~60분 전체 오차 {m_app:.2f}면으로, 기준선 ① 지금 값 유지({m_now:.2f}면)보다 {(m_now - m_app) / m_now * 100:.0f}% 작아요."
          + (f" 기준선 ② 패턴표({m_pat:.2f}면)보다도 작아요." if m_pat is not None and m_app < m_pat else "")]
    if m_pat is not None and m_app < m_pat and m_app < m_now:
        L.append("- 두 기준선을 모두 이겨서, 이 모델을 'AI 예측'이라고 부르는 조건(두 단순 예측보다 오차가 작을 것)을 채워요.")
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")


# ── 실행 ────────────────────────────────────────────────

def main():
    model = json.loads(em.ASSET_MODEL.read_text(encoding="utf-8"))
    golden = check_golden(model)
    cut = pd.Timestamp(model["data"]["to"])
    samples, _, _ = ds.load()
    te.add_cyclic(samples)
    lots = model["lots"]
    s = samples[(samples.t > cut) & samples.lot.isin(lots.keys())].reset_index(drop=True)
    if s.empty:
        sys.exit(f"모델을 만든 뒤({cut:%m-%d %H:%M}) 쌓인 기록이 아직 없어요.")
    # 주차장 번호와 노상 여부는 지금 기록이 아니라 모델 파일의 것을 써요(값이 멈춘 곳이 바뀌면 번호가 달라져요)
    s["lot_idx"] = s.lot.map({k: v["idx"] for k, v in lots.items()})
    s["onstreet"] = s.lot.map({k: v["onstreet"] for k, v in lots.items()}).astype(float)
    app = with_app_patterns(s)
    features = model["features"]
    d_app, p_app = predict(model, app[features].astype(float).to_numpy())
    d_ref, p_ref = predict(model, s[features].astype(float).to_numpy())
    th = model["full_risk"]["threshold"]

    preds = te.baselines(s)
    preds["app_model"], preds["ref_model"] = te.to_free(s, d_app), te.to_free(s, d_ref)
    metrics = te.evaluate(s, preds)
    full = {"app": te.evaluate_full(s, p_app, th), "ref": te.evaluate_full(s, p_ref, th)}

    days = []
    for day, idx in s.groupby(s.t.dt.date).indices.items():
        part = s.iloc[idx].reset_index(drop=True)
        m = te.evaluate(part, {k: v[idx] for k, v in preds.items()})
        f = te.evaluate_full(part, p_app[idx], th)
        days.append({"date": f"{day:%m/%d}({te.WEEKDAY_KO[day.weekday()]})", "n": len(part), "full": f["full_rows"],
                     "app": m["app_model"]["all"][0], "now": m["now"]["all"][0], "rule": f["rule"], "now_full": f["now_full"]})

    truth = s.target_free.to_numpy()
    err = pd.DataFrame({"lot": s.lot, "name": s.name, "total": s.total, "pattern": np.abs(preds["pattern"] - truth),
                        "now": np.abs(preds["now"] - truth), "app": np.abs(preds["app_model"] - truth)}).dropna(subset=["pattern"])
    by_lot = err.groupby(["lot", "name"]).agg(total=("total", "median"), pattern=("pattern", "mean"), now=("now", "mean"),
                                               app=("app", "mean"), psum=("pattern", "sum"))
    by_lot["share"] = by_lot.psum / err.pattern.sum() * 100
    worst = [{"name": name, "total": int(r.total), "pattern": r.pattern, "now": r.now, "app": r.app, "share": r.share}
             for (_, name), r in by_lot.sort_values("share", ascending=False).head(3).iterrows()]

    dates = sorted(set(s.t.dt.date))
    info = {"made": f"{pd.Timestamp.now():%Y-%m-%d %H:%M}", "model_made": model["made"], "model_from": model["data"]["from"],
            "model_to": model["data"]["to"], "from": f"{s.t.min():%Y-%m-%d %H:%M}", "to": f"{s.t_target.max():%Y-%m-%d %H:%M}",
            "days_text": f"{(s.t_target.max() - s.t.min()).total_seconds() / 86400:.1f}일, "
                         + "·".join(te.WEEKDAY_KO[d.weekday()] for d in dates) + "요일",
            "n": len(s), "lots": s.lot.nunique(), "model_lots": len(lots), "golden": golden, "threshold": th}
    write_report(info, metrics, full, days, worst)
    OUT.with_suffix(".json").write_text(json.dumps({"info": info, "mae": metrics, "full": full, "days": days}, ensure_ascii=False,
                                                   indent=1, default=str), encoding="utf-8")

    r, n0 = full["app"]["rule"], full["app"]["now_full"]
    print(f"채점 기간 {info['from']} ~ {info['to']}, 샘플 {len(s):,}개 (정답 사례 {golden}개 일치)")
    print(f"  만차 판단  앱 모델 precision {r['precision'] * 100:.1f}% · recall {r['recall'] * 100:.1f}% · F2 {r['f2']:.3f}"
          f" | 지금 만차만 F2 {n0['f2']:.3f}")
    for k in ("now", "pattern", "app_model"):
        print(f"  빈자리 오차  {k:<10} 30분 뒤 {te.fmt(metrics[k]['h30']):>6}면 · 전체 {te.fmt(metrics[k]['all']):>6}면")
    print(f"리포트: {OUT.relative_to(ds.ROOT)}")


if __name__ == "__main__":
    main()
