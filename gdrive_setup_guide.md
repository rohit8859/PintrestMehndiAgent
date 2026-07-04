# Google Drive API Setup Guide ☁️🔑

To enable the agent to upload Mehndi images to your Google Drive automatically, you need to create a project on the Google Cloud Console, enable the Google Drive API, and obtain a `credentials.json` file.

Follow these step-by-step instructions:

---

## Step 1: Create a Google Cloud Project

1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Log in with the Google Account you wish to use for Drive storage.
3. Click the **Project Dropdown** at the top left of the dashboard and select **New Project**.
4. Name your project (e.g., `Mehndi Downloader Agent`) and click **Create**.

---

## Step 2: Enable the Google Drive API

1. In the sidebar menu, navigate to **APIs & Services** > **Library**.
2. In the search bar, type `Google Drive API` and hit Enter.
3. Click on the **Google Drive API** result.
4. Click the blue **Enable** button.

---

## Step 3: Configure the OAuth Consent Screen

Because this app utilizes OAuth authentication to access your Drive, you must configure the OAuth Consent Screen.

1. Go to **APIs & Services** > **OAuth consent screen** from the left navigation bar.
2. Select **User Type**:
   * Choose **External** (this is standard for personal Google accounts).
   * Click **Create**.
3. Fill in the **App Information**:
   * **App name**: `Mehndi Downloader Agent`
   * **User support email**: Select your email address.
   * **Developer contact information**: Enter your email address.
   * Click **Save and Continue**.
4. **Scopes (Optional)**: Click **Save and Continue** (the application handles requesting scopes automatically).
5. **Test Users (Crucial)**:
   * Since your app is in "Testing" mode, only designated test users can authenticate.
   * Click **Add Users**, type your Google email address (the one where the images will be uploaded), and click **Add**.
   * Click **Save and Continue**, then review the summary and return to the dashboard.

---

## Step 4: Create OAuth Credentials

1. Go to **APIs & Services** > **Credentials** from the left navigation bar.
2. Click **+ Create Credentials** at the top and select **OAuth client ID**.
3. Choose the **Application type**: select **Desktop app**.
4. Name the client (e.g., `Mehndi Desktop Client`).
5. Click **Create**.
6. A popup will appear showing "OAuth client created". Click **OK**.
7. In the list of OAuth 2.0 Client IDs, locate your newly created client and click the **Download JSON** button (downward arrow icon) on the far right.
8. Save this file as `credentials.json` (make sure it is named exactly `credentials.json`) and place it directly inside the `pinterest_gdrive_agent/` root directory.

---

## Step 5: Authenticate on First Run

1. Run the agent (either from the Streamlit dashboard or CLI `python main.py --sync-all`).
2. The script will detect `credentials.json` and automatically open a browser window requesting you to sign in with your Google account.
   * *Note: Google may display a warning screen stating "Google hasn't verified this app". This is standard for sandbox development apps. Click **Advanced** and then **Go to Mehndi Downloader Agent (unsafe)***.
3. Grant permission to create and manage files that have been created by the app.
4. Once completed, a `token.json` file will be generated in your project folder. The agent is now fully authorized and will refresh this token automatically in the background on subsequent runs without requiring browser interaction.
