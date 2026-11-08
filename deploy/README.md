# Deploying Star Ground

Star Ground runs in two places (PLAN.md, "Hosting and cost"):

- **The API** runs on Google Cloud Run, scaling to zero, from the same Docker image GHCR publishes.
- **The site** is served by GitHub Pages at `star-ground.jacksonferguson.me`, built to call the API's Cloud Run URL.

CI selects checks from the changed files on pull requests and pushes to `main`. For example, a change to this guide runs the Markdown checks, a Python test change runs the Python suite, and a site change runs the web and container checks. The required `CI OK` check reports whether the selected checks passed. The file groups live in `.github/scripts/changes.sh`.

A push to `main` prepares a deployment if production inputs have changed since the last verified deploy. The run checks again after taking the deployment slot, when it also checks that its commit is still at the tip of `main`. This lets a later push carry a site change that was superseded while its checks ran. It also covers a revert after an interrupted deployment. If GitHub has no usable verification record, CI runs the full suite and prepares a deployment.

In `.github/workflows/deploy-production.yml`, `deploy-api` copies the exact `linux/amd64` image CI tested to Artifact Registry and deploys it. Then `deploy-site` builds the site against the API's URL and publishes it to Pages. Finally, `check-live` waits for Pages to serve that commit and pastes a parts list into the live site. GitHub records the result in the `production-verification` environment. The site goes live after the API, so it never calls routes the running API doesn't have yet. The three jobs hold the same deployment slot from start to finish.

Version tags (`v*`) no longer deploy. They publish the self-hosted image to GHCR (`.github/workflows/docker-publish.yml`), for people who pin a version. The hosted site has no versions: its visitors always get what's live.

The deploy jobs are skipped until the repository variable `GCP_PROJECT_ID` exists.

## One-time setup

Run these once, with the [gcloud CLI](https://cloud.google.com/sdk/docs/install) signed in as the project's owner. Set the names first; the rest of the commands read them:

```bash
PROJECT=star-ground-prod          # your project ID
REGION=us-central1                # a Cloud Run region
BILLING=000000-000000-000000      # gcloud billing accounts list
REPO=JacksonFergusonDev/star-ground
```

### 1. Project, APIs, and the budget alert

Cloud Run needs a billing account even inside the free tier. The budget alert emails the billing account's admins as spend passes 50% and 100% of $5. It doesn't stop spending: `--max-instances` in the workflow caps the rate, and the spending cutoff (step 6) stops it. The amount must be in the billing account's currency (`gcloud billing accounts describe`); Star Ground's account bills in CAD, so its budget is `7CAD`.

```bash
gcloud projects create "$PROJECT"
gcloud config set project "$PROJECT"
gcloud billing projects link "$PROJECT" --billing-account "$BILLING"
gcloud services enable --project "$PROJECT" \
  run.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com \
  sts.googleapis.com billingbudgets.googleapis.com
gcloud billing budgets create --billing-account "$BILLING" \
  --display-name "Star Ground" --budget-amount 5USD \
  --filter-projects "projects/$PROJECT" \
  --threshold-rule percent=0.5 --threshold-rule percent=1.0
```

### 2. The image registry and the service's account

Cloud Run can't pull from GHCR, so deploys copy the image into Artifact Registry. The service runs as its own account with no roles: the API calls no Google APIs. Builders' reports and bugs reach BigQuery through the service's logs, which need no key (see "Feedback and bug reports").

```bash
gcloud artifacts repositories create star-ground --project "$PROJECT" \
  --location "$REGION" --repository-format docker
gcloud iam service-accounts create star-ground-run --project "$PROJECT" \
  --display-name "Star Ground API"
```

### 3. Let GitHub Actions deploy, without a key

Workload identity federation lets deploy runs from this repository act as a deploy account, with no stored key. The provider accepts only tokens from this repository.

```bash
gcloud iam service-accounts create star-ground-deploy --project "$PROJECT" \
  --display-name "Star Ground deploys"
DEPLOY="star-ground-deploy@$PROJECT.iam.gserviceaccount.com"
RUNTIME="star-ground-run@$PROJECT.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding "$PROJECT" \
  --member "serviceAccount:$DEPLOY" --role roles/run.developer
gcloud artifacts repositories add-iam-policy-binding star-ground --project "$PROJECT" \
  --location "$REGION" --member "serviceAccount:$DEPLOY" --role roles/artifactregistry.writer
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" --project "$PROJECT" \
  --member "serviceAccount:$DEPLOY" --role roles/iam.serviceAccountUser

gcloud iam workload-identity-pools create github --project "$PROJECT" --location global
gcloud iam workload-identity-pools providers create-oidc star-ground --project "$PROJECT" \
  --location global --workload-identity-pool github \
  --issuer-uri https://token.actions.githubusercontent.com \
  --attribute-mapping google.subject=assertion.sub,attribute.repository=assertion.repository \
  --attribute-condition "assertion.repository == '$REPO'"
POOL=$(gcloud iam workload-identity-pools describe github --project "$PROJECT" \
  --location global --format 'value(name)')
gcloud iam service-accounts add-iam-policy-binding "$DEPLOY" --project "$PROJECT" \
  --member "principalSet://iam.googleapis.com/$POOL/attribute.repository/$REPO" \
  --role roles/iam.workloadIdentityUser
```

Then give the workflow the names it reads:

```bash
gh variable set GCP_PROJECT_ID --repo "$REPO" --body "$PROJECT"
gh variable set GCP_REGION --repo "$REPO" --body "$REGION"
gh variable set GCP_SERVICE_ACCOUNT --repo "$REPO" --body "$DEPLOY"
gh variable set GCP_WORKLOAD_IDENTITY_PROVIDER --repo "$REPO" --body "$POOL/providers/star-ground"
```

The deploy job runs in the `production` environment, which GitHub creates on the first deploy. To allow only `main` to deploy, add a deployment branch rule `main` under Settings, Environments, production.

### 4. Create the service and open it to the public

The site calls the API from visitors' browsers, so the service must accept unauthenticated requests. Deploys keep a service's access policy, so this is set once, here, on a placeholder that the first deploy replaces:

```bash
gcloud run deploy star-ground --project "$PROJECT" --region "$REGION" \
  --image us-docker.pkg.dev/cloudrun/container/hello \
  --service-account "star-ground-run@$PROJECT.iam.gserviceaccount.com" \
  --max-instances 1
gcloud run services add-iam-policy-binding star-ground --project "$PROJECT" \
  --region "$REGION" --member allUsers --role roles/run.invoker
```

### 5. GitHub Pages and DNS

1. Under Settings, Pages, set the source to **GitHub Actions** and the custom domain to `star-ground.jacksonferguson.me`. (`web/public/CNAME` records the domain too.)
1. Leave Settings, Environments, `github-pages` allowing only `main`, as GitHub creates it.
1. At the DNS host for `jacksonferguson.me`, add a `CNAME` record from `star-ground` to `jacksonfergusondev.github.io`.
1. Once GitHub has issued the certificate, turn on **Enforce HTTPS**.

### 6. The spending cutoff

Google Cloud has no hard spending limit, and three instances running flat out would cost about US$415 a month. A second budget is the stop. It publishes the month's spend to a Pub/Sub topic every few hours, and the function in [`cutoff/`](cutoff/main.py) closes the API to the internet (ingress `internal`) once the spend reaches it. The site stays up, and the paste page says the server is unreachable. Nothing is deleted. Billing data lags by hours, so spend can pass the budget before the cutoff acts.

The cutoff's account may update only the `star-ground` service, which needs read access to its image and permission to act as its runtime account:

```bash
NUMBER=$(gcloud projects describe "$PROJECT" --format 'value(projectNumber)')
CUTOFF="star-ground-cutoff@$PROJECT.iam.gserviceaccount.com"
gcloud services enable --project "$PROJECT" pubsub.googleapis.com \
  cloudfunctions.googleapis.com cloudbuild.googleapis.com eventarc.googleapis.com
gcloud pubsub topics create star-ground-budget --project "$PROJECT"
gcloud iam service-accounts create star-ground-cutoff --project "$PROJECT" \
  --display-name "Star Ground spending cutoff"
gcloud run services add-iam-policy-binding star-ground --project "$PROJECT" \
  --region "$REGION" --member "serviceAccount:$CUTOFF" --role roles/run.developer
gcloud iam service-accounts add-iam-policy-binding "star-ground-run@$PROJECT.iam.gserviceaccount.com" \
  --project "$PROJECT" --member "serviceAccount:$CUTOFF" --role roles/iam.serviceAccountUser
gcloud artifacts repositories add-iam-policy-binding star-ground --project "$PROJECT" \
  --location "$REGION" --member "serviceAccount:$CUTOFF" --role roles/artifactregistry.reader

gcloud functions deploy star-ground-cutoff --project "$PROJECT" --gen2 --region "$REGION" \
  --runtime python313 --source deploy/cutoff --entry-point cut_off \
  --trigger-topic star-ground-budget --service-account "$CUTOFF" \
  --set-env-vars "SERVICE=projects/$PROJECT/locations/$REGION/services/star-ground" \
  --max-instances 1 --memory 256Mi
# The trigger delivers as the cutoff's account, which must be allowed to call it.
gcloud run services add-iam-policy-binding star-ground-cutoff --project "$PROJECT" \
  --region "$REGION" --member "serviceAccount:$CUTOFF" --role roles/run.invoker

gcloud billing budgets create --billing-account "$BILLING" \
  --display-name "Star Ground cutoff" --budget-amount 20CAD \
  --filter-projects "projects/$NUMBER" --threshold-rule percent=1.0 \
  --notifications-rule-pubsub-topic "projects/$PROJECT/topics/star-ground-budget"
```

To test it, publish a notification over budget, check the API answers 404, then turn it back on:

```bash
gcloud pubsub topics publish star-ground-budget --project "$PROJECT" \
  --message '{"costAmount": 21, "budgetAmount": 20}'
```

**Turning the API back on** after a cutoff, once you've found what spent the money:

```bash
gcloud run services update star-ground --project "$PROJECT" --region "$REGION" --ingress all
```

Deploys keep the service's ingress, so a deploy doesn't undo a cutoff. The budget resets each month, but the service stays closed until you run that command.

### 7. Image cleanup

Every deploy copies an image of about 300 MB into Artifact Registry, which bills storage past 0.5 GB. Deploys share most of their layers, which the registry stores once. [`cleanup-policy.json`](cleanup-policy.json) keeps the ten newest images, so the last ten deploys can still be rolled back to:

```bash
gcloud artifacts repositories set-cleanup-policies star-ground --project "$PROJECT" \
  --location "$REGION" --policy deploy/cleanup-policy.json --no-dry-run
```

Deploys copy only the `linux/amd64` image, so each deploy is one image in the registry. Releases before v2.6.0 copied the whole multi-architecture index, whose parts the rule would count separately. So turn the rule on (`--no-dry-run`) only after a deploy from `main` has run.

## Deploying and rolling back

After a deploy, the run's summary links the deployed API, the `github-pages` environment links the site, and `https://star-ground.jacksonferguson.me/version.json` names the commit the site was built from. If `check-live` fails, the run keeps the browser's trace and screenshot as the `live-check` artifact.

To undo a bad change, revert it on `main`: the revert deploys like any other push. If the API is broken and can't wait for a deploy, send its traffic back to the last good revision, which takes seconds:

```bash
gcloud run revisions list --service star-ground --project "$PROJECT" --region "$REGION"
gcloud run services update-traffic star-ground --project "$PROJECT" --region "$REGION" \
  --to-revisions star-ground-00042-abc=100
```

While traffic is pinned to a revision, deploys create new revisions but don't send them traffic, so the fix you merge won't go live. Once it has deployed, return traffic to the newest revision:

```bash
gcloud run services update-traffic star-ground --project "$PROJECT" --region "$REGION" --to-latest
```

To publish a self-hosted image, tag a release as before (`scripts/prepare_release.py`, then push the `v*` tag).

The image serves the site itself when `STAR_GROUND_SITE_DIR` names it, which the Dockerfile sets for self-hosting. The deploy clears it, so the Cloud Run URL serves only the API, and allows only the Pages origin (`STAR_GROUND_CORS_ORIGINS`). It also sets:

- `STAR_GROUND_TRUST_PROXY=1`, so the rate limiter reads the client's address from `X-Forwarded-For`, where Cloud Run appends it as the last entry.
- `STAR_GROUND_OCR_THREADS=2`, matching `--cpu 2`. An instance reports 5 CPUs, and OCR sized to 5 threads ran slower than with 2.

The deploy updates these variables and keeps any others, so settings made by hand survive a deploy.

## Feedback and bug reports

Builders send two kinds of feedback from the workspace, with no account:

- **Bug reports.** When Star Ground hits a bug, the workspace offers **Send report**. It posts the bug's code, the document's name or link, and the builder's note.
- **Part reports.** In the document review, a builder who corrects a part can tell Star Ground: the document's link (or an upload's name and hash), the designator, what was read, what the document prints, and where on the page.

Neither ever holds the document itself.

On Cloud Run the API writes its logs as JSON (`src/star_ground/server/logs.py`). Each bug is logged with its traceback as an Error Reporting event, and each report as an entry with a `feedback` field whose `kind` is `bug` or `part`. A bug report carries the bug's code as the label `bug_code`, like the bug's own entry.

- **Error Reporting** in the Cloud console lists bugs grouped by cause, with counts and the release. To be emailed when a new one appears, open Error Reporting, choose **Configure notifications** and pick an email channel (make one under **Manage notification channels** if the list is empty).
- **The log sink `star-ground-feedback`** copies every report and every bug into the BigQuery dataset `feedback`, where they're kept for good. Logs themselves are kept 30 days. The sink writes to one table per day-partitioned log, `feedback.run_googleapis_com_stdout`.
- **The service account `star-ground-reader`** can query that dataset, read logs and view Error Reporting, and nothing else. It holds no key: the owner's `gcloud` login impersonates it, which is how agents read feedback (see `AGENTS.md`, "Reading feedback and bugs").

They were made once, by hand:

```bash
PROJECT=star-ground-api
gcloud services enable bigquery.googleapis.com clouderrorreporting.googleapis.com --project "$PROJECT"
bq --project_id="$PROJECT" mk --dataset --location=US "${PROJECT}:feedback"
gcloud logging sinks create star-ground-feedback \
  "bigquery.googleapis.com/projects/$PROJECT/datasets/feedback" \
  --project "$PROJECT" --use-partitioned-tables \
  --log-filter='resource.type="cloud_run_revision" AND resource.labels.service_name="star-ground" AND (jsonPayload.feedback.kind:* OR jsonPayload."@type"="type.googleapis.com/google.devtools.clouderrorreporting.v1beta1.ReportedErrorEvent")'
gcloud iam service-accounts create star-ground-reader --project "$PROJECT"
for role in roles/bigquery.jobUser roles/logging.viewer roles/errorreporting.viewer; do
  gcloud projects add-iam-policy-binding "$PROJECT" --condition=None \
    --member "serviceAccount:star-ground-reader@$PROJECT.iam.gserviceaccount.com" --role "$role"
done
gcloud iam service-accounts add-iam-policy-binding \
  "star-ground-reader@$PROJECT.iam.gserviceaccount.com" --project "$PROJECT" \
  --member "user:$(gcloud config get-value account)" --role roles/iam.serviceAccountTokenCreator
```

The dataset's access list then gets two entries (BigQuery's IAM bindings on a dataset need allowlisting, so they're edited as JSON with `bq show --format=prettyjson` and `bq update --source`): `WRITER` for the sink's writer identity (`gcloud logging sinks describe star-ground-feedback --format='value(writerIdentity)'`), and `READER` for `star-ground-reader`.

## Turning OCR off

OCR is the expensive part of the API: one document takes an instance's CPUs for 10 to 50 seconds. If it ever causes trouble, turn it off without a deploy. Image parts lists are then reported as images and never offered for OCR:

```bash
gcloud run services update star-ground --project "$PROJECT" --region "$REGION" \
  --update-env-vars STAR_GROUND_OCR=0
```

Turn it back on with `STAR_GROUND_OCR=1`. Each client's allowance (`STAR_GROUND_OCR_PER_HOUR`, 10 by default) can be changed the same way.
