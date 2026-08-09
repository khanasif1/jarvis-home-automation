# Google OAuth setup

Google integrations are optional. The backend starts without them, but tool
calls targeting Calendar, Tasks, or Gmail return a clear configuration error.

1. Create a Google Cloud project and an OAuth consent screen.
2. Enable only the APIs needed: Google Calendar, Google Tasks, and/or Gmail.
3. Create a web OAuth client. Set its redirect URI to the backend callback URL
   shown by the deployed Function App.
4. Store the client secret in Azure Key Vault. Configure the non-secret client
   ID and redirect URI as Function App settings; do not copy any OAuth value
   into source or Pi config.
5. Add test users while the consent screen remains in testing mode.
6. Start the backend authorization flow and grant the minimum requested scopes.
7. Confirm that the refresh credential is stored for the intended device in
   the `GoogleCredentials` table. Azure Storage encrypts the table at rest and
   access is limited to the Function App managed identity.
8. Confirm that no credential appears in Application Insights.

Recommended scopes:

| Capability | Scope |
| --- | --- |
| Read/write calendar events | `https://www.googleapis.com/auth/calendar.events` |
| Read/write tasks | `https://www.googleapis.com/auth/tasks` |
| Read-only Gmail search | `https://www.googleapis.com/auth/gmail.readonly` |

Do not request Gmail send/delete scopes unless a reviewed feature explicitly
requires them.
