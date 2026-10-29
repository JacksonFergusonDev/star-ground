# Deploying Star Ground

Star Ground runs in two places (PLAN.md, "Hosting and cost"):

- **The API** runs on Google Cloud Run, scaling to zero, from the same Docker image GHCR publishes.
- **The site** is served by GitHub Pages at `star-ground.jacksonferguson.me`, built to call the API's Cloud Run URL.

A release tag (`v*`) does both, in `.github/workflows/docker-publish.yml`. After the image is tested, signed and published to GHCR, the `deploy-api` job copies that exact image to Artifact Registry and deploys it. Then `deploy-site` builds the site against the API's URL and publishes it to Pages. The site goes live after the API, so it never calls routes the running API doesn't have yet.

Both deploy jobs are skipped until the repository variable `GCP_PROJECT_ID` exists, so releases still publish the image before Google Cloud is set up.

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

Cloud Run can't pull from GHCR, so releases copy the image into Artifact Registry. The service runs as its own account with no roles: the API calls no Google APIs. Feedback reports, which write to a Google Sheet, are left off until a later step adds their key as a secret.

```bash
gcloud artifacts repositories create star-ground --project "$PROJECT" \
  --location "$REGION" --repository-format docker
gcloud iam service-accounts create star-ground-run --project "$PROJECT" \
  --display-name "Star Ground API"
```

### 3. Let GitHub Actions deploy, without a key

Workload identity federation lets release runs from this repository act as a deploy account, with no stored key. The provider accepts only tokens from this repository.

```bash
gcloud iam service-accounts create star-ground-deploy --project "$PROJECT" \
  --display-name "Star Ground releases"
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

The deploy job runs in the `production` environment. GitHub creates it on the first release; to allow only release tags to deploy, add a deployment tag rule `v*` under Settings, Environments, production.

### 4. Create the service and open it to the public

The site calls the API from visitors' browsers, so the service must accept unauthenticated requests. Deploys keep a service's access policy, so this is set once, here, on a placeholder that the first release replaces:

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
1. Under Settings, Environments, `github-pages`, add a deployment tag rule `v*`. GitHub creates that environment allowing only `main`, and releases deploy from tags.
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

Releases keep the service's ingress, so a release doesn't undo a cutoff. The budget resets each month, but the service stays closed until you run that command.

### 7. Image cleanup

Every release copies an image of about 300 MB into Artifact Registry, which bills storage past 0.5 GB. [`cleanup-policy.json`](cleanup-policy.json) keeps the three newest images, so the last three releases can still be rolled back to:

```bash
gcloud artifacts repositories set-cleanup-policies star-ground --project "$PROJECT" \
  --location "$REGION" --policy deploy/cleanup-policy.json --no-dry-run
```

Releases copy only the `linux/amd64` image, so each release is one image in the registry. Releases before v2.6.0 copied the whole multi-architecture index, whose parts the rule would count separately. So turn the rule on (`--no-dry-run`) only after a release from v2.6.0 on has deployed.

## Releasing

Tag a release as before (`tools/prepare_release.py`, then push the `v*` tag). The run's summary links the deployed API, and the `github-pages` environment links the site.

The image serves the site itself when `STAR_GROUND_SITE_DIR` names it, which the Dockerfile sets for self-hosting. The deploy clears it, so the Cloud Run URL serves only the API, and allows only the Pages origin (`STAR_GROUND_CORS_ORIGINS`). It also sets:

- `STAR_GROUND_TRUST_PROXY=1`, so the rate limiter reads the client's address from `X-Forwarded-For`, where Cloud Run appends it as the last entry.
- `STAR_GROUND_OCR_THREADS=2`, matching `--cpu 2`. An instance reports 5 CPUs, and OCR sized to 5 threads ran slower than with 2.

The deploy updates these variables and keeps any others, so settings made by hand survive a release.

## Feedback and bug reports

When Star Ground hits a bug reading a document, the workspace offers a **Send report** button. It posts the bug's code, its traceback, the document's name or link, and the builder's note to the Reports tab of the "Star Ground Feedback" Google Sheet. Feedback ratings go to the same sheet's first tab. Both need the sheet's service-account key, the one the Streamlit app reads from its secrets. Without it, the routes answer 503 and the workspace offers only the GitHub issue link.

Run these yourself, since they handle the key. Save the service account's JSON key as `feedback-key.json`, outside the repository, then:

```bash
gcloud services enable secretmanager.googleapis.com --project "$PROJECT"
gcloud secrets create star-ground-feedback --project "$PROJECT" \
  --replication-policy automatic --data-file feedback-key.json
gcloud secrets add-iam-policy-binding star-ground-feedback --project "$PROJECT" \
  --member "serviceAccount:star-ground-run@$PROJECT.iam.gserviceaccount.com" \
  --role roles/secretmanager.secretAccessor
gcloud run services update star-ground --project "$PROJECT" --region "$REGION" \
  --update-secrets STAR_GROUND_FEEDBACK_CREDENTIALS=star-ground-feedback:latest
rm feedback-key.json
```

The sheet must be shared with the service account's email as an editor; the Streamlit app's already is. The Reports tab is created on the first report. Releases keep the secret, as they keep other settings made by hand.

Each bug is also written to Cloud Run's log as `Bug <code>: …` with its traceback, so a report can be matched to its log entry:

```bash
gcloud logging read 'resource.labels.service_name="star-ground" AND textPayload:"Bug A1B2C3"' \
  --project "$PROJECT" --freshness 30d
```

## Turning OCR off

OCR is the expensive part of the API: one document takes an instance's CPUs for 10 to 50 seconds. If it ever causes trouble, turn it off without a release. Image parts lists are then reported as images and never offered for OCR:

```bash
gcloud run services update star-ground --project "$PROJECT" --region "$REGION" \
  --update-env-vars STAR_GROUND_OCR=0
```

Turn it back on with `STAR_GROUND_OCR=1`. Each client's allowance (`STAR_GROUND_OCR_PER_HOUR`, 10 by default) can be changed the same way.
