# Outlook OAuth setup

ApplicationTrackr reads Outlook / Microsoft 365 mail through Microsoft Graph using OAuth2 device-code flow. It does **not** store the Microsoft account password.

## One-time Microsoft Entra setup

1. Go to Microsoft Entra admin center and create an **App registration**.
2. Choose an account type that includes the Microsoft account you use for Outlook. If this is a personal Outlook/Hotmail account, the registration must support personal Microsoft accounts.
3. Under **Authentication**, enable public-client flows / mobile and desktop flows so device-code authentication is allowed.
4. Under **API permissions**, add delegated Microsoft Graph permission **Mail.Read**.
5. Copy the app's **Application (client) ID**.

## Portainer variables

```text
OUTLOOK_USER=you@example.com
MICROSOFT_CLIENT_ID=<Application client ID>
MICROSOFT_TENANT=common
```

No `OUTLOOK_APP_PASS`, IMAP host, or IMAP port is required.

## First login

After redeploying, the email-listener log will print a Microsoft device-login URL and a short code. Open the URL on any device, enter the code, and approve access.

ApplicationTrackr then stores the MSAL token cache at:

```text
/data/microsoft_msal_cache.json
```

Because `/data` is a persistent Docker volume, normal restarts/redeploys retain the login. On later polls MSAL tries silent token acquisition and refreshes access tokens from the cached credentials automatically.

You normally **do not sign in every time**. Interactive sign-in is needed again only if the cached authorization is removed/revoked, Microsoft requires fresh consent/authentication, the app registration changes, or the persistent `/data` volume/token cache is deleted.

## Permissions

The app requests only delegated `Mail.Read` for inbox scanning. It does not request mail-send permission.
