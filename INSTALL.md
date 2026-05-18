# Install Guide - Property Analysis V1

This file walks through the first install from the zip package.

The normal setup is:

1. Unzip the package.
2. Install Python if needed.
3. Create a local Python environment.
4. Install the required packages.
5. Run the health check.
6. Start the web app.

Follow the steps in order. Run each command from PowerShell unless noted.

## 1. Unzip The Package

1. Find `joe-property-analysis-v1.zip`.
2. Right-click the zip file.
3. Click **Extract All**.
4. Choose a simple location, such as:

```text
C:\Users\<your-name>\Documents\joe-property-analysis
```

5. Open the extracted folder.

Do not run the tool from inside the zip preview window. The folder must be fully extracted first.

The extracted folder should contain these items:

```text
.claude
scripts
templates
webapp
output
INSTALL.md
JOE_USER_MANUAL.md
requirements.txt
```

## 2. Check Python

Open PowerShell and run:

```powershell
python --version
```

If it prints `Python 3.11` or newer, continue to Section 3.

If PowerShell says Python is not recognized, or the version is older than 3.11:

1. Go to `https://www.python.org/downloads/windows/`.
2. Download the latest Windows installer.
3. Run the installer.
4. On the first installer screen, check **Add python.exe to PATH**.
5. Click **Install Now**.
6. When the installer finishes, close PowerShell.
7. Open a new PowerShell window.
8. Run this again:

```powershell
python --version
```

Continue only after PowerShell shows Python 3.11 or newer.

## 3. Open PowerShell In The Tool Folder

In PowerShell, move into the extracted package folder.

Example:

```powershell
cd C:\Users\<your-name>\Documents\joe-property-analysis
```

If your folder is somewhere else, use that path instead.

Confirm you are in the right folder:

```powershell
dir
```

You should see `requirements.txt`, `scripts`, `templates`, and `webapp`.

## 4. Allow Local Activation Scripts

Windows may block the command that activates the local Python environment.
Run this once on the machine:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

If PowerShell asks for confirmation, type:

```text
Y
```

This setting applies only to your Windows user account.

## 5. Create The Local Python Environment

From the tool folder, run:

```powershell
python -m venv .venv
```

This creates a private Python environment inside the project folder.

When it finishes, activate it:

```powershell
.\.venv\Scripts\Activate.ps1
```

After activation, the PowerShell prompt should start with `(.venv)`.

If activation fails, confirm Section 4 was completed, then close PowerShell, reopen it, return to the tool folder, and try the activation command again.

## 6. Install The Required Packages

With `(.venv)` showing in the prompt, run:

```powershell
pip install -r requirements.txt
```

This step downloads and installs the Python packages used by the tool.
The first install requires internet access.

When the command finishes, continue to the health check.

## 7. Run The Health Check

Run:

```powershell
python -m scripts.property_analysis.agent_check
```

A healthy install ends with:

```text
ALL AGENT CHECKS PASSED
```

The check also runs the regression suite. This can take a little while.

If a package is missing, rerun:

```powershell
pip install -r requirements.txt
python -m scripts.property_analysis.agent_check
```

Do not use the tool for production deals until the health check passes.

## 8. Start The Web App

From the same PowerShell window, run:

```powershell
python -m uvicorn webapp.main:app --port 8000
```

Leave this PowerShell window open while using the tool.

Open a browser and go to:

```text
http://127.0.0.1:8000/
```

To stop the web app, go back to the PowerShell window and press:

```text
Ctrl+C
```

## 9. Run The First Test Deal

Use the web app home page to create and analyze this test property:

```text
Address: 1417 S Canal St, Pittsburgh, PA 15215
Purchase price: 130000
ARV: 255000
Rehab: 55000
Bedrooms: 3
```

Expected result:

```text
Verdict: CONSIDER
Cap rate: about 9.6%
DSCR: about 1.23x
IRR: about 16%
```

The run creates a deal packet under:

```text
output\projects\Joe Berlin\<address>\<YYYY-MM-DD_HHMMSS>\
```

Each packet contains:

```text
*_proforma.xlsx
*_report.md
*_sources.json
```

## 10. Optional Command Line Test

You can run the same test without the web app:

```powershell
python -m scripts.property_analysis.analyze --address "1417 S Canal St, Pittsburgh, PA 15215" --price 130000 --arv 255000 --rehab 55000 --beds 3
```

This writes the same packet files and prints the markdown report in PowerShell.

## 11. Data And Internet Notes

Bundled in the zip:

- HUD FY2026 FMR and SAFMR rent database
- ZIP-to-county geography database
- Excel proforma template
- Joe reference workbook
- empty deal intake database

Basic use does not require an API key.

The first package install requires internet access because Python downloads dependencies.

For Pittsburgh-area properties, the tool can call Allegheny County/WPRDC for parcel facts and tax estimates. That lookup uses the public internet unless the property is already cached. To turn that lookup off for a session, run this before starting the web app or CLI:

```powershell
$env:WPRDC_MODE = "off"
```

RentCast is off by default. It will not run unless a RentCast API key is added later.

## 12. Common Issues

### `python` is not recognized

Python is not installed or was installed without PATH enabled. Reinstall Python and check **Add python.exe to PATH** on the first installer screen.

### `Activate.ps1 cannot be loaded`

Run Section 4:

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

Then reopen PowerShell and try again.

### The browser says the page cannot be reached

Make sure the PowerShell window running `uvicorn` is still open. If it is closed, start the web app again:

```powershell
python -m uvicorn webapp.main:app --port 8000
```

### The report says `DIAGNOSTIC ONLY`

The tool is missing a required input. Read the blocker list at the top of the report, fill in the missing information, and run the deal again.

Common blockers are:

- missing ARV
- missing rehab budget
- missing property tax
- missing rent
- ambiguous ZIP or county

### The app starts, but another process is already using port 8000

Run the app on a different port:

```powershell
python -m uvicorn webapp.main:app --port 8001
```

Then open:

```text
http://127.0.0.1:8001/
```

## 13. Updating Later

If Sean sends a replacement zip later:

1. Stop the web app with `Ctrl+C`.
2. Back up the current folder.
3. Unzip the new package.
4. Copy your current `output\projects\Joe Berlin\reference_data.xlsx` into the new package if needed.
5. Run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m scripts.property_analysis.agent_check
```

Use the new package only after the health check passes.
