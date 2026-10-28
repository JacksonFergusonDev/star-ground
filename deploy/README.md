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

Cloud Run needs a billing account even inside the free tier. The budget alert emails the billing account's admins as spend passes 50% and 100% of $5. It doesn't stop spending; `--max-instances` in the workflow is the hard ceiling.

```bash
gcloud projects create "$PROJECT"
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
1. At the DNS host for `jacksonferguson.me`, add a `CNAME` record from `star-ground` to `jacksonfergusondev.github.io`.
1. Once GitHub has issued the certificate, turn on **Enforce HTTPS**.

## Releasing

Tag a release as before (`tools/prepare_release.py`, then push the `v*` tag). The run's summary links the deployed API, and the `github-pages` environment links the site.

The image serves the site itself when `STAR_GROUND_SITE_DIR` names it, which the Dockerfile sets for self-hosting. The deploy clears it, so the Cloud Run URL serves only the API, and allows only the Pages origin (`STAR_GROUND_CORS_ORIGINS`). It also sets `STAR_GROUND_TRUST_PROXY=1`, so the rate limiter reads the client's address from `X-Forwarded-For`; PLAN.md step 4 still asks to confirm that Cloud Run appends it as the last entry.
