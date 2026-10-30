# Live data (not committed)

Put real CSV exports here, using the same file names and columns as `data/demo/`.
Everything in this folder except this README is ignored by git.

Start the app in live mode:

```bash
OPS_DATA_MODE=live streamlit run app.py
```

The Data Health page will list any missing columns, invalid dates or unknown status values.
