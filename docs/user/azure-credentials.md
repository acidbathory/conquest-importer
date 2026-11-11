# Azure credentials: create a read-only service principal

This guide is for a person who has never done this. It creates one identity (a "service principal")
that can only read your Azure Monitor setup: alert rules, action groups, alert processing rules,
dashboards and workbooks. It cannot create, change or delete anything.

The Azure migration is not built yet (see [Datadog and Azure](runbook-datadog-and-azure.md)); this
page prepares the credentials so they are ready. Nothing here calls the migration tool.

How each step is marked:

- VERIFIED: the step or fact was read on a Microsoft Learn page on 2026-11-11; the URL is given.
- UNVERIFIED: written from memory or worked out from other pages. Check it on screen before relying on it.

Nothing in this guide was run against a real Azure tenant. Portal screens change; menu names below are
the ones on the fetched pages.

## 0. What you end up with

Four values, which go in the git-ignored `.env` file next to the tool (never in a document, a chat
or a commit):

```text
AZURE_TENANT_ID=<directory (tenant) id>
AZURE_CLIENT_ID=<application (client) id>
AZURE_CLIENT_SECRET=<the secret value, shown once>
AZURE_SUBSCRIPTION_ID=<subscription id>
```

These four names are the planned names. The tool does not read them yet (UNVERIFIED future
behaviour). For a national cloud see section 8.

## 1. Who is allowed to do this

Two different permissions are needed, and often they belong to two different people.

| You need to | Permission | Source |
|---|---|---|
| Register the application in Microsoft Entra ID | "at least an Application Developer" (VERIFIED). A page on the portal route says "at least a Cloud Application Administrator" and the `Application.ReadWrite.All` permission (VERIFIED, differing pages). If the directory setting "Users can register applications" is No, you must hold Application Developer, Application Administrator, Cloud Application Administrator, Global Administrator or Hybrid Identity Administrator (VERIFIED) | https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app , https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal , https://learn.microsoft.com/en-us/cli/azure/azure-cli-sp-tutorial-1 |
| Give that application a role on a subscription | `Microsoft.Authorization/roleAssignments/write`, "such as Role Based Access Control Administrator or User Access Administrator" (VERIFIED). The Owner role has the action `*` and "the ability to assign roles in Azure RBAC" (VERIFIED). Contributor does not appear on the page for this (VERIFIED by absence; UNVERIFIED as a statement about Contributor) | https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal , https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/privileged |

If the "Add role assignment" button is greyed out, you are not allowed to assign roles (VERIFIED, the
assignment page says the option is disabled). If creating the application fails with "Insufficient
privileges to complete the operation", ask your Microsoft Entra administrator (VERIFIED, CLI page).

Ask your administrator for the two things in this order: "register an app named
`migration-readonly`" and "assign it the Reader role on subscription `<name>`". Section 9 is a
message you can send.

## 2. Which role is enough

Recommended: **Reader**, assigned at **subscription** scope.

What was read (VERIFIED, https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/general
and https://learn.microsoft.com/en-us/azure/role-based-access-control/built-in-roles/monitor):

| Role | Id | Actions | What it covers for us |
|---|---|---|---|
| Reader | `acdd72a7-3385-48ef-bd42-f606fba81ae7` | `*/read` only, no write, no data actions | Every Azure resource's control-plane read: alert rules (metric, log, activity log), action groups, alert processing rules, Prometheus rule groups, dashboards, workbooks |
| Monitoring Reader | `43d0d8ad-25c7-4714-9337-8ba259a9fe05` | `*/read`, `Microsoft.OperationalInsights/workspaces/search/action` (run a log search), `Microsoft.Support/*` (create and update a support ticket) | The same reads, plus running log queries. It also lets a principal open support tickets, so it is not strictly read-only |
| Workbook Reader | `b279062a-9be3-42a0-92ae-8b3cf002ec4d` | `microsoft.insights/workbooks/read`, `microsoft.insights/workbooks/revisions/read`, `microsoft.insights/workbooktemplates/read` | Workbooks only. Not needed if Reader is assigned |

Why Reader covers the objects (UNVERIFIED as a live test; derived): `*/read` is "Read control plane
information for all Azure resources", and every object we read is an Azure Resource Manager
resource under `Microsoft.Insights`, `Microsoft.AlertsManagement` or `Microsoft.Portal`. Azure
Resource Graph needs only "at least read access to the resources you want to query" (VERIFIED,
https://learn.microsoft.com/en-us/azure/governance/resource-graph/overview), which Reader gives.

What Reader does not give (VERIFIED by the action list): no log query. If a later step must run a
KQL query to see what data exists, add Monitoring Reader then, and say why. New Relic's own Azure
integration asks for both Reader and Monitoring Reader (see `docs/research/azure-datapath.md`), but
that is New Relic's polling, not this tool.

Warning on secrets (UNVERIFIED): a read of an action group returns its webhook URLs. In the official
examples they are in the response in clear text (https://github.com/Azure/azure-rest-api-specs, the
action group examples). Whether a real tenant hides them is not confirmed. Treat everything the
tool exports as sensitive, and do not paste exports into chats.

Narrower than Reader: a custom role with only the specific read actions is possible. The action
names were not fetched, so this guide does not list them (UNVERIFIED).

## 3. Portal steps

### 3.1 Register the application (VERIFIED)

Source: https://learn.microsoft.com/en-us/entra/identity-platform/quickstart-register-app and
https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal

1. Sign in to the Microsoft Entra admin center as at least an Application Developer.
2. If you have access to several tenants, use the Settings icon in the top menu to pick the tenant
   that owns the subscription.
3. Browse to **Entra ID > App registrations** and select **New registration**.
4. Name: for example `migration-readonly`.
5. Supported account types: **Single tenant only** (the page recommends this for most applications).
6. Redirect URI: the portal-route page asks you to choose "Web" and enter a URI. A script that signs
   in with its own secret does not use a redirect. Leaving it empty is the usual choice for that
   (UNVERIFIED). New Relic's own guide uses `https://newrelic.com` as a placeholder (VERIFIED in the
   New Relic docs); any harmless URL works.
7. Select **Register**. A service principal is created automatically with the registration (VERIFIED).

### 3.2 Copy the three ids (VERIFIED)

On the application's **Overview** page copy:

- **Application (client) ID** into `AZURE_CLIENT_ID`;
- **Directory (tenant) ID** into `AZURE_TENANT_ID`.

The subscription id: search for **Subscriptions** in the Azure portal, open the one you want, copy
**Subscription ID** into `AZURE_SUBSCRIPTION_ID` (the portal route is VERIFIED on the role page; the
exact field label is UNVERIFIED). Or run `az account show --query id --output tsv` (VERIFIED,
https://learn.microsoft.com/en-us/azure/governance/resource-graph/first-query-rest-api).

### 3.3 Create the client secret (VERIFIED)

Source: https://learn.microsoft.com/en-us/entra/identity-platform/how-to-add-credentials

1. Open the app registration, then **Certificates & secrets**.
2. **Client secrets > New client secret**.
3. Description: for example `migration-readonly 2026-11`.
4. Expiry: pick a short one. Microsoft says the lifetime is limited to 24 months and
   "recommends that you set an expiration value of less than 12 months". Pick 3 to 6 months for a
   migration that takes weeks.
5. **Add**. Copy the secret **Value** (not the "Secret ID") into `AZURE_CLIENT_SECRET` straight away.
   "This secret value is never displayed again after you leave this page." If you lose it, create a
   new secret and delete the old one.

Microsoft also says a certificate is recommended over a secret for production, and that secrets are
"less secure" and fine for local development (VERIFIED, same page). This tool plans to use a secret
(section 0); a short expiry is the compensation.

### 3.4 Assign the Reader role on the subscription (VERIFIED)

Source: https://learn.microsoft.com/en-us/entra/identity-platform/howto-create-service-principal-portal
and https://learn.microsoft.com/en-us/azure/role-based-access-control/role-assignments-portal

1. In the Azure portal search for **Subscriptions**, select your subscription.
2. In the left pane select **Access control (IAM)**.
3. **Add > Add role assignment**.
4. On the **Role** tab choose **Reader**, then **Next**.
5. On the **Members** tab, **Assign access to: User, group, or service principal**, then
   **Select members**. "By default, Microsoft Entra applications aren't displayed", so type the
   application's name to find it.
6. **Select**, then **Review + assign**.

Do the same for every subscription you want read. A role on a subscription is inherited by its
resource groups and resources (VERIFIED, "Permissions are inherited to lower levels of scope").

New Relic's guide also asks you to check that the resource provider `microsoft.insights` is
`Registered` on the subscription (VERIFIED in the New Relic docs). It is normally already
registered if anyone has used alerts; check under **Subscription > Resource providers**.

## 4. The same with the Azure CLI

The portal steps are the safest for a first time. The CLI does steps 3.1, 3.3 and 3.4 in one command
(VERIFIED, https://learn.microsoft.com/en-us/cli/azure/azure-cli-sp-tutorial-1):

```bash
az login
az account show --query "{subscriptionId:id, tenantId:tenantId}" --output json
az ad sp create-for-rbac --name "migration-readonly" \
  --role reader \
  --scopes /subscriptions/00000000-0000-0000-0000-000000000000
```

Replace the zero subscription id with yours. The output looks like this (VERIFIED, same page; the
values here are the page's placeholders):

```json
{
  "appId": "myAppId",
  "displayName": "myServicePrincipalName",
  "password": "myServicePrincipalPassword",
  "tenant": "myTentantId"
}
```

Map: `appId` is `AZURE_CLIENT_ID`, `password` is `AZURE_CLIENT_SECRET`, `tenant` is `AZURE_TENANT_ID`.
Warnings on that page, all VERIFIED:

- The output holds credentials. "Be sure that you do not include these credentials in your code or
  check the credentials into your source control." Run the command in a terminal whose history and
  output are not shared.
- The command can modify an existing application or service principal if another one has the same
  display name. Names are not unique. Use a name nobody else uses.
- You need User Access Administrator or Role Based Access Control Administrator permission, or
  higher, to run it.
- You cannot read the password again. Reset it with `az ad sp credential reset` if lost.

The command's default secret lifetime is not stated on the fetched page (UNVERIFIED). After running
it, open the secret in the portal and check its expiry, or create the secret yourself as in 3.3.

## 5. Test it with one read-only call

This needs `curl`. It asks for a token, then lists metric alert rules in the subscription. Both calls
only read. Replace the placeholders; keep the secret in an environment variable so it does not end up
in your shell history.

```bash
# Put the values in the current shell only (do not paste the secret into a saved file).
export AZURE_TENANT_ID="<tenant id>"
export AZURE_CLIENT_ID="<client id>"
read -rs AZURE_CLIENT_SECRET && export AZURE_CLIENT_SECRET   # type the secret, it is not echoed
export AZURE_SUBSCRIPTION_ID="<subscription id>"

TOKEN=$(curl -sS -X POST \
  "https://login.microsoftonline.com/${AZURE_TENANT_ID}/oauth2/v2.0/token" \
  --data-urlencode "client_id=${AZURE_CLIENT_ID}" \
  --data-urlencode "client_secret=${AZURE_CLIENT_SECRET}" \
  --data-urlencode "scope=https://management.azure.com/.default" \
  --data-urlencode "grant_type=client_credentials" \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["access_token"])')

curl -sS -H "Authorization: Bearer ${TOKEN}" \
  "https://management.azure.com/subscriptions/${AZURE_SUBSCRIPTION_ID}/providers/Microsoft.Insights/metricAlerts?api-version=2026-01-01"
```

What is VERIFIED here:

- The token request shape (POST to `https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token`
  with `client_id`, `scope`, `client_secret`, `grant_type=client_credentials`) and that the scope is
  the resource identifier "suffixed with `.default`" (https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-client-creds-grant-flow ; the page's example is for Microsoft Graph).
- The `Authorization: Bearer <token>` header (https://learn.microsoft.com/en-us/rest/api/azure/).
- Azure Resource Manager's base address `https://management.azure.com/` (same page).
- The list path and the api-version `2026-01-01` (the swagger in Azure/azure-rest-api-specs, see
  [the alerting note](../research/azure-monitor-alerting.md)).

UNVERIFIED: that `https://management.azure.com/.default` is the exact scope string for Resource
Manager (it follows the page's rule but the page does not print it for Resource Manager).

Reading the result:

- `{"value": [ ... ]}` (possibly an empty list) means it works. An empty list means "no metric alert
  rules in this subscription", which is also a valid answer.
- Error `AADSTS7000215` or "invalid client secret" in the token step: the secret is wrong or you
  copied the Secret ID instead of the Value (error text from memory, UNVERIFIED).
- HTTP 403 "AuthorizationFailed": the Reader role is missing on this subscription, or was assigned a
  few minutes ago (propagation time is UNVERIFIED).
- `NoRegisteredProviderFound` or a provider error: `Microsoft.Insights` is not registered
  (UNVERIFIED wording).

To test other object types, replace the path with one from
[the alerting note](../research/azure-monitor-alerting.md) (section 2). Do not run PUT, PATCH,
DELETE or POST calls with this principal; Reader would refuse them anyway.

## 6. What it costs

Status: **not checked** for dollar amounts.

The Azure Monitor pricing page was fetched (https://azure.microsoft.com/en-us/pricing/details/monitor/)
but every price in its HTML is shown as `$-`, so no figure can be read from it. What the page does
state about structure (VERIFIED, no amounts):

- Activity log alert rules: free units "Limited to 100 rules per subscription".
- Metric alert rules: "10 monitored metric time-series per month" are included; beyond that a price
  per monitored time series.
- Log alert rules: the first time series is included in the rule; prices differ by evaluation
  frequency (1, 5, 10, 15 minutes). Prometheus metrics alerts are "only charged the cost for the
  Prometheus Metrics query".
- Notifications: 1,000 emails, 1,000 push notifications and 100,000 webhooks per month are free; SMS
  and voice calls are priced per country.
- The page lists no charge for an action group itself, only for the notifications it sends
  (UNVERIFIED reading of the page).

So a handful of test alert rules inside those free units may cost nothing, but that is an
inference, not a checked number. Reading with the read-only principal creates no rules and sends no
notifications. Whether API read calls are billed was not found (UNVERIFIED). Ask the owner of the
subscription to look at Azure Cost Management after a test.

## 7. Secret expiry and rotation

- Set a calendar reminder for two weeks before the expiry date shown under **Certificates &
  secrets** (the date is visible in the portal; VERIFIED that expiry is chosen at creation).
- To rotate: create a new secret (3.3), put the new value in `.env`, run the test in section 5, then
  delete the old secret. Rotating with two secrets alive at once is allowed (UNVERIFIED but the
  portal lists several).
- When the migration is finished, revoke everything (section 10).

## 8. National clouds (Azure Government, China)

The global-cloud addresses above do not work for national clouds. Each cloud has its own portal and
its own sign-in address (VERIFIED, https://learn.microsoft.com/en-us/entra/identity-platform/authentication-national-cloud):

| Cloud | Portal | Token endpoint base | Resource Manager base |
|---|---|---|---|
| Global | https://portal.azure.com | https://login.microsoftonline.com | https://management.azure.com (VERIFIED) |
| US Government | https://portal.azure.us | https://login.microsoftonline.us | `https://management.usgovcloudapi.net` (UNVERIFIED: not found on the fetched pages) |
| China (21Vianet) | https://portal.azure.cn | https://login.partner.microsoftonline.cn | `https://management.chinacloudapi.cn` (VERIFIED in the `az cloud show` output on https://learn.microsoft.com/en-us/cli/azure/manage-clouds-azure-cli) |

You must register the application separately in each cloud's portal (VERIFIED, same page). With the
CLI run `az cloud set --name AzureUSGovernment` (VERIFIED, https://learn.microsoft.com/en-us/azure/azure-government/documentation-government-get-started-connect-with-cli)
or `AzureChinaCloud` before `az login`.

The scope for the token request in a national cloud is the Resource Manager address of that cloud
followed by `/.default` (UNVERIFIED, same reasoning as the global scope).

A planned `.env` variable `AZURE_CLOUD` (values `AzureCloud`, `AzureUSGovernment`, `AzureChinaCloud`,
the names the OpenTelemetry Azure Monitor receiver uses for its `cloud` setting) is a proposal
only, not decided.

New Relic's own Azure integrations cannot read Azure Government resources (VERIFIED in the New
Relic docs; see [the data path note](../research/azure-datapath.md)). That limits where converted
alerts can get their data, not whether this tool can read the alert rules.

## 9. A message for your administrator

> Please create a read-only identity for an Azure Monitor migration assessment.
> 1. In Microsoft Entra ID, register an application named `migration-readonly`, single tenant, and
>    create one client secret that expires in 3 months. Send me the Application (client) ID and the
>    Directory (tenant) ID in one message, and the secret value in a different channel.
> 2. Assign that application the built-in **Reader** role on subscription `<subscription name or id>`.
>    No other role is needed.
> It will only read alert rules, action groups, alert processing rules, dashboards and workbooks.
> We will delete the secret and the role assignment when the assessment is over.

## 10. How to revoke

Do all three, in this order, when the migration is finished or if the secret leaks.

1. **Delete the secret.** Portal: app registration > **Certificates & secrets** > delete the secret.
   CLI (VERIFIED, https://learn.microsoft.com/en-us/cli/azure/ad/sp/credential):
   `az ad sp credential delete --id <client id> --key-id <secret key id>`.
2. **Remove the role assignment.** Portal: subscription > **Access control (IAM)** > **Role
   assignments**, remove the row (the removal menu is UNVERIFIED on the fetched pages). CLI
   (VERIFIED, https://learn.microsoft.com/en-us/cli/azure/role/assignment):
   `az role assignment delete --assignee <client id> --scope /subscriptions/<subscription id>`.
   The page recommends running `az role assignment list` with the same arguments first, because the
   delete removes every assignment that matches.
3. **Delete the application** if it will not be used again. Portal: the **Delete** button on the app
   registration (VERIFIED in the New Relic uninstall steps, which delete their registered app the same
   way). `az ad sp delete --id <client id>` removes the service principal (VERIFIED command exists
   on https://learn.microsoft.com/en-us/cli/azure/ad/sp); removing the application object with the
   CLI is `az ad app delete` (UNVERIFIED, page not fetched).

Then remove the four lines from `.env` and check that no copy of the secret is in a shell history,
a document or a chat.

After step 1 a token already issued stays valid until it expires (UNVERIFIED; commonly about an hour).
