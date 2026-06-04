# italent-salary-feishu-sync

Automated salary data synchronization from Beisen iTalent HR system to Feishu (Lark) Bitable.

## Overview

This tool automatically fetches salary data from the Beisen iTalent HR system via browser automation (Playwright) and syncs it to a Feishu Bitable spreadsheet. It runs on a daily schedule (default: 22:32) and handles three separate tables:

| Order | Bitable Sheet | Script | Data Source | Sync Rule |
|-------|--------------|--------|-------------|-----------|
| 1 | Wage Standard | `sync_wage_standard.py` | All Salary Archives | Match by employee ID; update if changed |
| 2 | Salary Data | `sync_table1.py` | All Salary Archives | Incremental insert by employee ID + effective date; never overwrite |
| 3 | Salary Adjustment Records | `sync_table3.py` | All Salary Archives | Pair adjacent records and insert; never overwrite |

---

## Requirements

- **OS**: Windows 10 or above
- **Python**: 3.10+
- **Network**: Access to Beisen iTalent HR system and Feishu Open Platform

---

## Installation

### Step 1: Install Python (skip if already installed)

1. Download from https://www.python.org/downloads/
2. During installation, **check** `Add Python to PATH`
3. Verify: open PowerShell and run `python --version`

### Step 2: Clone the Repository

```bash
git clone https://github.com/your-username/italent-salary-feishu-sync.git
cd italent-salary-feishu-sync
```

Or download the ZIP and extract.

### Step 3: Run Setup Script

1. Open the project folder
2. **Double-click `setup.bat`**
3. Wait for automatic installation (Playwright browser download takes ~2-5 minutes on first run)

> **Manual installation** (if `setup.bat` fails):
> ```
> pip install -r requirements.txt
> python -m playwright install chromium
> ```

### Step 4: Configure

1. Copy `config.example.py` to `config.py`
2. Edit `config.py` with your actual credentials:

```python
# HR System (Beisen iTalent)
HR_LOGIN_URL = "https://your-company.italent.cn/Login"
HR_USERNAME  = "your_email@company.com"
HR_PASSWORD  = "your_password"

# Feishu App Credentials
FEISHU_APP_ID     = "your_feishu_app_id"
FEISHU_APP_SECRET = "your_feishu_app_secret"

# Bitable App Token (the string after base/ in the URL)
FEISHU_BASE_ID    = "your_base_id"

# Table IDs for each sheet
FEISHU_TABLE1_ID  = "your_table1_id"
FEISHU_TABLE2_ID  = "your_table2_id"
FEISHU_TABLE3_ID  = "your_table3_id"

# Feishu user IDs to receive sync completion notifications
FEISHU_NOTIFY_USER_IDS = ["user_id_1", "user_id_2"]

# Scheduled sync time (24-hour format)
SYNC_TIME = "22:32"
```

> **Important**:
> - `config.py` is excluded by `.gitignore` and will not be committed to Git
> - The HR account must have access to the Compensation & Benefits module in iTalent
> - The Feishu app must have Bitable read/write and messaging permissions

---

## Usage

### Option A: Run a Single Table (for testing)

```powershell
cd project-path
python sync_wage_standard.py    # Table 1: Wage Standard
python sync_table1.py           # Table 2: Salary Data
python sync_table3.py           # Table 3: Salary Adjustment Records
```

Run each table individually first to verify before starting the scheduled service.

### Option B: Start the Scheduled Service

**Double-click `start_service.bat`** to run the service. It syncs all three tables daily at the configured time.

Or run from PowerShell:
```powershell
cd project-path
python main.py
```

> **Note**:
> - Closing the terminal window stops the service
> - The service will not run if the computer is shut down or in sleep/hibernate mode
> - Recommended: disable automatic sleep and set up auto-start on boot

### Option C: Auto-Start on Boot (recommended)

**Double-click `auto_start_setup.bat`** and select "Set up auto-start."

The script creates a shortcut in the Windows Startup folder. The service will start minimized on login. To remove, run the same script and select "Remove auto-start."

---

## Table Logic Details

### Table 1: Wage Standard (`sync_wage_standard.py`)

Maintains one summary row per employee with current, probation, and post-confirmation salary data.

**Data source**: HR system "All Salary Archives"

**Fields**:

| Bitable Field | Source | Notes |
|--------------|--------|-------|
| Employee ID | Employee ID | Primary key |
| Name | Employee | — |
| Department | Department | — |
| Position | Position | — |
| Currency | Offer Currency | — |
| [Current] Salary Package | Latest record by effective date | — |
| [Current] Fixed Salary | Latest record | Daily rate package = intern daily rate × 22 |
| [Current] Performance Bonus | Latest record | Monthly performance base; if empty, quarterly ÷ 3 |
| [Current] Bonus Base | Latest record | Monthly bonus base; if empty, quarterly ÷ 3 |
| [Probation] Salary Package | Record with change reason = "Initial Offer" | Empty if not found |
| [Probation] Fixed Salary | Record with change reason = "Initial Offer" | Daily rate × 22 if applicable |
| [Probation] Bonus Base | Record with change reason = "Initial Offer" | — |
| [Post-Confirmation] Salary Package | Record with change reason = "Confirmation Adjustment" | Empty if not found |
| [Post-Confirmation] Fixed Salary | Record with change reason = "Confirmation Adjustment" | Daily rate × 22 if applicable |
| [Post-Confirmation] Performance Bonus | Record with change reason = "Confirmation Adjustment" | Monthly; if empty, quarterly ÷ 3 |
| [Post-Confirmation] Bonus Base | Record with change reason = "Confirmation Adjustment" | — |

**Sync rule**: Match by employee ID. If the employee exists, compare all fields and update if changed; otherwise insert.

---

### Table 2: Salary Data (`sync_table1.py`)

Records every salary history entry per employee (one employee may have multiple rows).

**Data source**: HR system "All Salary Archives"

**Fields**:

| Bitable Field | HR Column | Special Logic |
|--------------|-----------|---------------|
| Employee ID | Employee ID | — |
| Name | Employee | Reads `.staff-name` element to avoid avatar placeholder text |
| Department | Department | — |
| Position | Position | — |
| Currency | Offer Currency | — |
| Fixed Salary | Base Salary | Daily rate package = intern daily rate × 22 |
| Performance Bonus | Monthly Performance Base | If empty, quarterly ÷ 3 |
| Bonus Base | Monthly Bonus Base | If empty, quarterly ÷ 3 |
| Salary Package | Salary Package | — |
| Change Reason | Change Reason | — |
| Effective Date | Effective Date | Converted to Unix timestamp (ms) |

**Sync rule**: Key = Employee ID + Effective Date. Skip if exists; insert if new. Append-only, never overwrite.

---

### Table 3: Salary Adjustment Records (`sync_table3.py`)

Records before/after comparisons for each salary adjustment.

**Data source**: HR system "All Salary Archives"

**Generation logic**:
- Group by employee ID, sort by effective date within each group
- Pair adjacent records: earlier = "before", later = "after"
- 3 records → 2 adjustment rows; N records → N-1 rows
- Employees with only 1 record are skipped

**Fields**:

| Bitable Field | Source | Notes |
|--------------|--------|-------|
| Employee ID / Name / Dept / Position / Currency | "After" record | — |
| Change Reason | "After" record's change reason | — |
| [Before] Salary Package / Fixed / Performance / Bonus | Earlier record | Same calculation rules as above |
| [After] Salary Package / Fixed / Performance / Bonus | Later record | Same calculation rules as above |
| Effective Date | "After" record's effective date | Unix timestamp (ms) |

**Sync rule**: Key = Employee ID + "After" Effective Date. Skip if exists; insert if new. Append-only.

---

## Shared Calculation Rules

Applied consistently across all three tables:

| Field | Calculation |
|-------|------------|
| Fixed Salary | If salary package contains "daily rate" → intern daily rate × 22; otherwise use base salary |
| Performance Bonus | Monthly performance base preferred; if empty, quarterly performance base ÷ 3 |
| Bonus Base | Monthly bonus base preferred; if empty, quarterly bonus base ÷ 3 |
| Effective Date | Converted to Unix timestamp in milliseconds when writing to Feishu |

---

## Sync Completion Notification

After each scheduled sync, the system sends a Feishu message to the configured users with:

- Sync timestamp
- Per-table results (inserted / updated / skipped counts)
- Error details if any table failed

Example notification:

```
Salary Bitable Sync Completed

Sync time: 2026-06-04 22:35

✅ Wage Standard: 5 new, 3 updated, 53 skipped
✅ Salary Data: 2 new, 65 skipped
✅ Adjustment Records: 1 new, 5 skipped

Check logs/sync.log for details if any issues.
```

To modify recipients, edit `FEISHU_NOTIFY_USER_IDS` in `config.py`.

---

## Project Structure

```
italent-salary-feishu-sync/
├── .gitignore             — Git ignore rules (config.py, logs, etc.)
├── config.example.py      — Config template (copy to config.py and fill in values)
├── setup.bat              — One-click setup (first-time use)
├── start_service.bat      — Start the scheduled sync service
├── auto_start_setup.bat   — Set up / remove auto-start on boot
├── main.py                — Scheduler entry point (daily at configured time)
├── config.py              — All configuration (credentials, IDs) [gitignored]
├── hr_client.py           — HR system data extraction (Playwright browser automation)
├── feishu_client.py       — Feishu Bitable API client (read/write/notify)
├── sync_wage_standard.py  — Table 1: Wage Standard sync logic
├── sync_table1.py         — Table 2: Salary Data sync logic
├── sync_table3.py         — Table 3: Salary Adjustment Records sync logic
├── logger.py              — Logging utility
├── screenshot_manager.py  — Screenshot cleanup utility
├── requirements.txt       — Python dependencies
├── README.md              — This documentation
├── logs/                  — Runtime logs (auto-created)
│   └── sync.log
└── screenshots/           — Debug screenshots (auto-created, periodically cleaned)
```

---

## Logs

Logs are saved to `logs/sync.log` and can be viewed with any text editor.

Key log entries:

| Log Message | Meaning |
|------------|---------|
| `开始同步 [1-工资标准]` | Table 1 sync started |
| `HR 原始数据共 XX 行` | Total records read from HR system |
| `新增: XX 条` | Records newly inserted into Feishu |
| `更新: XX 条` | Records updated (Table 1 only) |
| `已存在跳过: XX 条` | Existing records skipped |
| `同步完成` | Sync completed successfully |
| `同步失败` | Sync failed (error details follow) |

---

## Troubleshooting

### Q1: setup.bat says "Python not detected"
Ensure Python is installed with `Add Python to PATH` checked. Restart PowerShell after installation.

### Q2: Login failed
Check `HR_USERNAME` and `HR_PASSWORD` in `config.py`. The account must have access to the Compensation & Benefits module.

### Q3: HR system returns no data
Possibly a page structure change or network timeout. Temporarily change `headless=True` to `headless=False` in `hr_client.py` to observe the browser automation visually.

### Q4: Feishu write failed
Verify:
- `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are correct
- The Feishu app has `bitable:app` and `bitable:record:write` permissions
- `FEISHU_BASE_ID` and all `TABLE_ID` values match the actual Bitable

### Q5: Record count is wrong (expected 67 but got 55)
The "All Salary Archives" tab was not successfully switched to. Check the log for `全部档案视图: ✅`. If missing, the tab click may need timing adjustments.

### Q6: Change the scheduled time
Edit `SYNC_TIME` in `config.py` (24-hour format), then restart `main.py`.

### Q7: No Feishu notification received
Verify:
- `FEISHU_NOTIFY_USER_IDS` contains valid Feishu user IDs
- The Feishu app has `im:message` permission enabled
- Check logs for "飞书通知发送失败" error messages

### Q8: Service stops after reboot
Run `auto_start_setup.bat` to configure auto-start on boot.

---

## Feishu App Permissions

If setting up a new Feishu app, enable the following permissions:

1. Log in to the Feishu Open Platform (https://open.feishu.cn)
2. Navigate to the app's permission settings
3. Enable:
   - `bitable:app` — Access Bitable
   - `bitable:app:readonly` — Read Bitable records
   - `bitable:record:write` — Write Bitable records
   - `im:message` — Send messages (for sync notifications)
4. Add the app as a collaborator in the Bitable's advanced permissions

---

## HR System Page Requirements

> ⚠️ **Important: Do not modify the column layout of the iTalent salary archive pages**

This tool reads data by column order in the HR system's table. The following actions on the "Latest Salary Archives" or "All Salary Archives" views **may cause data misalignment or sync failures**:

- **Deleting columns**: Shifts all subsequent column positions, causing field mapping errors
- **Hiding columns**: Same effect as deleting — changes visible column order
- **Reordering columns**: Dragging columns to different positions breaks field mapping
- **Excessive horizontal scrolling**: The table uses virtual rendering; columns outside the viewport may not be readable

Expected column order (left to right):

```
Employee | ID | Department | Position | Effective Date | Salary Package |
Offer Currency | Change Reason | Base Salary | Monthly Bonus Base |
Quarterly Performance Base | Quarterly Bonus Base | Monthly Performance Base |
Intern Daily Rate | Actions
```

If the column layout must be changed for business reasons, notify the development team to update the field mapping logic in the scripts.

---

## Security Notes

- `config.py` contains HR credentials and Feishu app secrets — treat it as sensitive
- Never share `config.py` via email, chat, or any unencrypted channel
- When deploying on multiple machines, use separate HR accounts for each
- Rotate HR passwords periodically and update `config.py` accordingly

---

## License

MIT
