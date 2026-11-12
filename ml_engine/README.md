# Aab-o-Hisaab AI — Challenge C02: Severe Class Imbalance / Rare-Event Stress

Flood prediction pipeline and 99:1 rare-event stress verification for **Aab-o-Hisaab AI**.

## Final 99:1 Evaluation Results (`reports/test_results.json`)
- **Evaluation Split (`2019–2023`, 99:1 ratio):** `272,500` rows (`2,725` positives, `269,775` negatives — exact `99.0:1`)
- **PR-AUC (Average Precision):** **`0.8445` (`0.845`)** — **PASS** (`>= 0.70` required)
  - Slow-onset mode (`Sindh/Punjab`): `0.8202`
  - Flash-flood mode (`KP/Balochistan/AJK/GB`): `0.8553`
- **Precision / Recall / F1 (at validation threshold `0.3419`):** Precision = `0.5011`, Recall = `0.8517`, F1 = `0.6310`
- **Confusion Matrix:** `TN = 267,464`, `FP = 2,311`, `FN = 404`, `TP = 2,321`
- **Event Detection:** `51 / 53` (`96.2%`) of flood event windows and `795 / 918` (`86.6%`) of district flood episodes detected in advance.

## Re-running the Pipeline
```powershell
python src/data/s01_districts.py
python src/data/s02_fetch_nasa_power.py
python src/data/s04_flood_events.py
python src/data/s05_build_dataset.py
python src/data/s06_validate.py
python src/models/make_splits.py
python src/models/train.py
python src/models/evaluate.py
python src/inference/flood_model.py
```
