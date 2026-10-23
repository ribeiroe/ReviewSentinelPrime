# Review Sentinel Prime v2.1.2 — User Guide

![Review Sentinel Prime logo](../../static/images/logo.png)

> **You do not need to be a developer to use this app.**
> Follow this guide step by step and you will have everything running in minutes.
> You cannot break anything — every step can be undone.

---

## Table of Contents

1. [What is Review Sentinel Prime?](#1-what-is-review-sentinel-prime)
2. [What is Docker Desktop?](#2-what-is-docker-desktop)
3. [Step 1 — Install Docker Desktop](#3-step-1--install-docker-desktop)
4. [Step 2 — Make sure Docker is running](#4-step-2--make-sure-docker-is-running)
5. [Step 3 — Start the app](#5-step-3--start-the-app)
6. [Step 4 — Open the app in your browser](#6-step-4--open-the-app-in-your-browser)
7. [How to stop the app](#7-how-to-stop-the-app)
8. [How to start it again later](#8-how-to-start-it-again-later)
9. [How to completely remove everything](#9-how-to-completely-remove-everything)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What is Review Sentinel Prime?

**Review Sentinel Prime (RSP)** is a tool for researchers who need to search scientific databases as part of a **Systematic Literature Review (SLR)**.

Instead of visiting Google Scholar, IEEE, PubMed, arXiv, and others one by one, this app lets you search up to **twelve databases at the same time** from a single screen — and automatically removes duplicate results.

**Everything runs on your own computer.** Your search history, API keys, and results never leave your machine.

---

## 2. What is Docker Desktop?

Docker Desktop is a small program that creates a safe, self-contained space on your computer where the app can run. Think of it like a pre-configured virtual machine that runs silently in the background.

You only install it once. After that, starting or stopping the app is as simple as double-clicking a file.

**Docker Desktop is free to download and use for personal and academic research.**

---

## 3. Step 1 — Install Docker Desktop

### Windows

1. Go to **[https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)**
2. Click the big **"Download for Windows"** button.
3. Open the downloaded file (`Docker Desktop Installer.exe`) and follow the on-screen instructions.
4. When asked, leave all the default options selected and click **Next** until the installation finishes.
5. Restart your computer if prompted.

### macOS

1. Go to **[https://www.docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop)**
2. Click **"Download for Mac"** — choose **Apple Chip** if your Mac was bought in 2020 or later, or **Intel Chip** if it was bought before 2020. (If you are unsure, try Apple Chip first.)
3. Open the downloaded `.dmg` file and drag the Docker icon into your **Applications** folder.
4. Open Docker from your Applications folder.

### Linux (Ubuntu/Debian)

Open a Terminal and paste these commands one at a time:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
```

Then log out and log back in, or restart your computer.

---

## 4. Step 2 — Make sure Docker is running

Before you can start the app, Docker Desktop must be running.

### Windows (check Docker status)

- Look at the **taskbar** at the bottom-right of your screen (the area near the clock).
- You should see a small **whale icon** 🐳.
- If the icon is **animated** (moving), Docker is still starting — wait a minute.
- If the icon is **still**, Docker is ready.
- If you do not see the icon, open **Docker Desktop** from the Start Menu.

### macOS (check Docker status)

- Look at the **menu bar** at the top-right of your screen.
- You should see a small **whale icon** 🐳.
- If it is animated, wait until it stops moving.
- If you do not see it, open **Docker Desktop** from your Applications folder.

### Linux (check Docker status)

- Open a Terminal and type: `docker info`
- If you see information about Docker (lots of text), it is running.
- If you see an error, start Docker with: `sudo systemctl start docker`

---

## 5. Step 3 — Start the app

Make sure you have **extracted the ZIP file** to a folder on your computer (e.g. your Desktop or Documents folder). Do not run the app from inside the ZIP file.

### Windows (start app)

1. Open the folder where you extracted the app.
2. **Double-click `start-app.bat`**.
3. A black window will open and show progress messages.
4. **The very first time**, the app needs to prepare itself. This takes **3–5 minutes**. Please wait — do not close the window.
5. When it is ready, your browser will open automatically.

### macOS (start app)

1. Open the folder where you extracted the app.
2. **Double-click `start-app.command`** — macOS will open a Terminal window and start the app automatically.
   - If you see a security warning, right-click the file, choose **Open**, then click **Open** again.
3. The very first time takes **3–5 minutes**. Please wait.
4. When it is ready, your browser will open automatically.

### Linux (start app)

1. Open a Terminal in the app folder.
2. Make the scripts runnable once: `chmod +x start-app.command stop-app.command`
3. Run it: `./start-app.command`

---

## 6. Step 4 — Open the app in your browser

Once the app has started, your browser should open automatically to:

**[http://localhost:8090](http://localhost:8090)**

If your browser does not open automatically, open it yourself (Chrome, Firefox, Edge, or Safari all work) and type this address in the address bar:

```text
http://localhost:8090
```

> **Tip:** Bookmark this address so you can open the app instantly next time.

---

## 7. How to stop the app

When you are done working, stop the app to free up your computer's resources.

### Windows (stop app)

- Double-click **`stop-app.bat`** in the app folder.
- A window will appear briefly and confirm the app has stopped.

### macOS (stop app)

- **Double-click `stop-app.command`** in the app folder.

### Linux (stop app)

- In a Terminal, run:
   `./stop-app.command`

> **Your data is safe.** All your saved searches, API keys, and results are stored in the `data/` folder on your computer. They are never deleted when you stop the app.

---

## 8. How to start it again later

Starting the app a second time is much faster — **usually under 10 seconds** — because the first-time setup is already done.

Just **double-click `start-app.bat`** (Windows) or **double-click `start-app.command`** (macOS) again.

---

## 9. How to completely remove everything

If you ever want to remove Review Sentinel Prime from your computer:

1. Stop the app first (see section 7 above).
2. Open Docker Desktop.
3. Go to **Images** in the left sidebar and delete the image named `reviewsentinel`.
4. Delete the app folder from your computer.
5. Optionally, uninstall Docker Desktop from your system like any other program.

> **Your saved searches and results** are in the `data/` folder inside the app folder. If you want to keep them, copy that folder somewhere safe before deleting the app.

---

## 10. Troubleshooting

### "Docker Desktop is not running"

→ Open Docker Desktop from your Start Menu (Windows) or Applications folder (macOS).
→ Wait until the whale icon stops animating, then try again.

---

### "Something went wrong" during startup

→ Make sure Docker Desktop is fully open (not just opening).
→ Try double-clicking `start-app.bat` again — it is safe to run multiple times.
→ If the error persists, take a screenshot of the window and contact support.

---

### The browser opens but shows an error page or connection refused

The browser opened while the app was still loading, or another program is using port 8090.
→ Wait 30 seconds and press **F5** (or `Cmd+R` on macOS) to refresh the page.
→ If still failing, check if port 8090 is in use by another application and close it.

---

### The app opens but looks broken (no images or styling)

→ Press **Ctrl+Shift+R** (Windows/Linux) or **Cmd+Shift+R** (macOS) to do a hard refresh.

---

### I closed the start window — is the app still running?

Yes. Once started, the app runs in the background even if you close the launch window.
→ Open your browser and go to **[http://localhost:8090](http://localhost:8090)** to check.

---

### The app is slow on first start

This is normal. Docker needs to download and prepare components the first time.
→ This only happens once. Future starts take under 10 seconds.

---

### Port 8090 is already in use

Another program on your computer is using port 8090.
→ Close other local apps or servers, then try starting the app again.
→ On macOS/Linux you can free the port with: `kill -9 $(lsof -ti :8090)`

---

### "Test Connection" shows an error for my API key

The Settings page tests all API connections through the app's backend (not directly from the browser). If a key is rejected:
→ Check the error message — it includes the exact reason and a link to the provider's portal.
→ Common causes: key not yet approved (IEEE), key expired, or wrong key pasted.
→ HAL, arXiv, and OpenAlex do not require keys — they should always connect successfully. ACM Digital Library also needs no key, but ACM blocks automated server-side access, so its search may report an explicit error instead of results.

---

### I need help

Please contact the support team and include:

- A screenshot of the error message.
- Your operating system (Windows 10/11, macOS version, Linux distribution).

---

Review Sentinel Prime v2.1.2 — Systematic Literature Review Automation Platform
