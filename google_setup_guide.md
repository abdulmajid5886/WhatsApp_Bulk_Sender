# How to Resolve `credentials.json not found`

To use the Google Contacts feature, you need to create a project in the Google Cloud Console and download your OAuth 2.0 credentials.

### Step 1: Create a Google Cloud Project
1. Go to the [Google Cloud Console](https://console.cloud.google.com/).
2. Click on the project dropdown at the top and select **"New Project"**.
3. Give it a name like `WhatsApp Bulk Sender` and click **Create**.

### Step 2: Enable the People API
1. In the sidebar, go to **APIs & Services** > **Library**.
2. Search for **"Google People API"**.
3. Click it and then click **Enable**.

### Step 3: Configure OAuth Consent Screen
1. Go to **APIs & Services** > **OAuth consent screen**.
2. Choose **External** and click **Create**.
3. Fill in the **App name**, **User support email**, and **Developer contact information**.
4. Click **Save and Continue** through the Scopes and Test Users pages (you don't need to add anything yet).

### Step 4: Create OAuth 2.0 Credentials
1. Go to **APIs & Services** > **Credentials**.
2. Click **+ Create Credentials** > **OAuth client ID**.
3. Select **Application type**: **Desktop app**.
4. Give it a name (e.g., `Desktop Client`) and click **Create**.
5. A popup will show your client ID and secret. Click **OK**.

### Step 5: Download and Rename
1. In the **OAuth 2.0 Client IDs** list, find your new client and click the **Download JSON** icon (down arrow) on the right.
2. Save the file as **`credentials.json`** (exactly that name, no capitalization).
3. Move `credentials.json` into your project folder: `/Volumes/Personal/Github/whatsApp_sender/`.

### Step 6: Restart the App
- Run `python3 main.py` again.
- Clicking "Connect Google Account" should now open your browser for authentication.
