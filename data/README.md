# Data Directory

Place the following files here before running the pipeline:

## Required Files
- CAM_1.mp4   (Skincare zone camera)
- CAM_2.mp4   (Makeup zone camera)
- CAM_3.mp4   (Entry/exit door camera)
- CAM_4.mp4   (Stockroom camera)
- CAM_5.mp4   (Billing counter camera)
- pos_transactions.csv  (Brigade Road POS data — rename from Brigade_Bangalore_10_April_26.csv)

## Setup Command
```bash
cp /path/to/Brigade_Bangalore_10_April_26.csv data/pos_transactions.csv
cp /path/to/CAM_*.mp4 data/
```