# Jun Yadnap Trade System

ASX 200 systematic trading dashboard. Auto-updates daily at 4:15pm Melbourne time.

---

## Setup Instructions

### Step 1 — Create the repository

1. Go to **github.com** and log in as `rjunpandey-netizen`
2. Click the **+** icon (top right) → **New repository**
3. Name it exactly: `jyts`
4. Set to **Public** (required for free GitHub Pages)
5. Click **Create repository**

---

### Step 2 — Upload the files

1. In your new repository, click **uploading an existing file**
2. Upload these files maintaining the folder structure:
   ```
   scripts/build.py
   .github/workflows/build.yml
   README.md
   ```
3. Click **Commit changes**

To upload with folder structure:
- Click **Add file → Upload files**
- Drag the entire `jyts` folder contents
- Or use the GitHub web editor to create each file manually (paste the contents)

---

### Step 3 — Enable GitHub Pages

1. Go to your repository → **Settings** tab
2. Scroll to **Pages** (left sidebar)
3. Under **Source** → select **Deploy from a branch**
4. Branch: **gh-pages** → folder: **/ (root)**
5. Click **Save**

---

### Step 4 — Run the first build manually

1. Go to **Actions** tab in your repository
2. Click **Build Jun Yadnap Trade System** (left sidebar)
3. Click **Run workflow** → **Run workflow**
4. Wait ~60 seconds for it to complete (green tick)

---

### Step 5 — Access your dashboard

Your dashboard URL will be:
```
https://rjunpandey-netizen.github.io/jyts/
```

Bookmark this. Open it every day at 4:15pm Melbourne time.

**Password:** `Youarewhoyouthinkyouare`

---

## Daily routine

| Time (AEST/AEDT) | Action |
|---|---|
| 4:00pm | ASX closes |
| 4:15pm | Open dashboard URL — auto-updated |
| 4:16pm | Enter password → read Signal Card |
| 4:18pm | Check GEAR or BBOZ price on SelfWealth |
| 4:20pm | Log paper trade in dashboard |
| 4:25pm | Done |

---

## How it works

- GitHub Actions runs the Python script every weekday at 4:15pm Melbourne time
- Script fetches ASX 200 data via Yahoo Finance (server-side — no CORS issues)
- Calculates SMA20, SMA250, RSI, Bollinger Bands
- Determines regime and signal
- Builds and publishes the HTML dashboard automatically
- You just open the URL — data is already there

---

## Files

| File | Purpose |
|---|---|
| `scripts/build.py` | Main Python script — fetches data, builds HTML |
| `.github/workflows/build.yml` | GitHub Actions — runs build.py daily |
| `README.md` | This file |

---

*Not financial advice. Paper trading simulation only.*
