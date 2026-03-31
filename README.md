# Movie Notification Bot for 🩸👹🏯Demon Slayer Infinity Castle Arc Movie💀🗿📈

A Python-based bot that automatically checks BookMyShow for new Japanese 4DX showtimes of the Demon Slayer: Infinity Castle arc and sends instant notifications to Telegram. This project uses GitHub Actions for continuous, automated monitoring.

## ✨ Features

- **Automated Scanning:** The bot runs on a schedule (every 5 minutes) to scan for new showtimes.
- **Specific Monitoring:** Focuses on Japanese 4DX shows at PVR Irrum Manzil and PVR Nexus Mall in Hyderabad.
- **Instant Telegram Notifications:** Sends a direct message to your Telegram account when a new showtime is detected.
- **Set and Forget:** Runs on GitHub Actions, so no local machine needs to be kept on.
- **Secure Credentials:** All sensitive data is stored securely using GitHub Secrets.

## 🛠️ Setup

Follow these steps to set up the bot for your own use.

### 1. Create a Telegram Bot

1.  Open the Telegram app and search for **@BotFather**.
2.  Start a chat with BotFather and use the `/newbot` command.
3.  Follow the prompts to name your bot and create a unique username (ending in `_bot`).
4.  BotFather will give you an **HTTP API Token**. Copy this token.
5.  Start a chat with your new bot by searching for its username and clicking "Start."

### 2. Get your Telegram Chat ID

1.  Search for **@RawDataBot** on Telegram and send it any message.
2.  The bot will respond with a JSON object. Your Chat ID is the number associated with the `id` key inside the `chat` object.

### 3. Create a GitHub Repository

1.  Create a **public GitHub repository**. This is necessary to use GitHub Actions for free.
2.  Push all your project files to this repository:
    - `demon_slayer_bot.py` (your Python script)
    - `requirements.txt` (dependencies)
    - `.gitignore` (ignores local files)
    - `.github/workflows/schedule.yml` (the GitHub Actions workflow)

### 4. Configure GitHub Secrets

This is the most critical step for security. You must add your credentials as secrets in your repository.

1.  Go to your GitHub repository on the web.
2.  Click **Settings > Secrets and variables > Actions**.
3.  Click **New repository secret** and add the following:
    - **Name:** `TELEGRAM_API_TOKEN`, **Value:** Your bot's API token.
    - **Name:** `TELEGRAM_CHAT_ID`, **Value:** Your Telegram Chat ID.
    - **Name:** `MOVIE_CODE`, **Value:** `movie code from url`
    - **Name:** `CITY_CODE`, **Value:** `your city name`

### 5. Confirm the GitHub Actions workflow

1.  Go to the **Actions** tab in your repository.
2.  You should see the `Check Showtimes` workflow listed.
3.  The workflow will run automatically every 5 minutes according to the schedule defined in `schedule.yml`.
4.  To test it immediately, click on the workflow and then click **Run workflow**.

## 🚀 How to Use

Once the setup is complete, the bot will automatically run every 5 minutes. You will receive a Telegram notification whenever a new Japanese 4DX showtime is added for the specified cinemas.

## 🚨 Disclaimer

- This script is intended for personal, non-commercial use only.
- Web scraping can be sensitive to website changes. If the BookMyShow website's structure changes, the script may need to be updated.
- Use at your own risk. This project is not affiliated with or endorsed by BookMyShow.

---

<div align="center">

**Built with 💻 and ☕ by Vishal Goud**

[![LinkedIn](https://img.shields.io/badge/LinkedIn-blue?style=flat&logo=linkedin)](http://www.linkedin.com/in/vishalgoud3105)
[![GitHub](https://img.shields.io/badge/GitHub-black?style=flat&logo=github)](https://github.com/Vishalgoud3105)
[![Portfolio](https://img.shields.io/badge/Portfolio-orange?style=flat)](https://vishalgoud3105.github.io/Portfolio/)

---

### 📬 Contact

This repository is **private**. For collaboration inquiries, demo requests, or questions:

📧 **Email**: [vishalgoud3105@gmail.com](mailto:vishalgoud3105@gmail.com)  
💼 **LinkedIn**: [vishalgoud](http://www.linkedin.com/in/vishalgoud3105)  
🌐 **Portfolio**: [vishalgoud3105.github.io](https://vishalgoud3105.github.io/Portfolio/)

⭐ **Interested in this project? Reach out!** ⭐
