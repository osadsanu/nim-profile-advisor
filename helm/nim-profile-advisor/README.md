# NIM Profile Advisor - HPE Private Cloud AI framework

Helm chart that deploys the NIM Profile Advisor (website + MongoDB) as an imported framework in HPE Private Cloud
AI (HPE AI Essentials). It follows *Preparing the Framework Helm Chart* of the HPE AI Essentials 2026.07.1
documentation: an `ezua` VirtualService for the application endpoint, `hpe-ezua` labels on every pod and an optional
Istio authorization policy for single sign-on.

| Resource | Purpose |
| --- | --- |
| Deployment `<release>-nim-profile-advisor` | Website. On its first start it imports the NIM catalog bundled in the image (`SEED_IMPORT`); the Load NIMs tab runs `nim_profile_finder_v2.sh` in this pod |
| StatefulSet `<release>-nim-profile-advisor-mongodb` | MongoDB 8 with a 5 Gi volume |
| Secret `<release>-nim-profile-advisor-env` | The `env` values (MongoDB credentials, NGC API key, settings) |
| VirtualService (`templates/ezua/`) | `nim-advisor.${DOMAIN_NAME}` through `istio-system/ezaf-gateway` |
| Kyverno ClusterPolicy (pre-install hook) | Adds the `hpe-ezua` labels to the pods of this release only (see below) |
| AuthorizationPolicy (`pcai.sso: true`) | PCAI single sign-on through oauth2-proxy |

## Package

```bash
helm lint helm/nim-profile-advisor --set env.MONGO_ROOT_PASSWORD=lint
helm package helm/nim-profile-advisor        # -> nim-profile-advisor-0.1.0.tgz
```

The *PCAI Helm chart* GitHub Actions workflow also publishes the package and the logo as the `pcai-framework`
artifact.

## Import into PCAI

1. **Tools & Frameworks > Import Framework**.
2. **Import Chart**: upload the icon `helm/nim-profile-advisor.png` and the `.tgz` file. Keep the project namespace
   or enable *Use Custom Namespace*.
3. **Edit Chart**: framework name *NIM Profile Advisor*, a release name (e.g. `nim-advisor`), category *Data Science*,
   enable *Wait*.
4. **Review Config**: leave *Apply Helm Chart Config* (AutoHelm) off; the chart brings its own VirtualService and
   labels. Replace the placeholders in `env`:
   - `MONGO_ROOT_PASSWORD` (required: the deployment fails while it is `<MONGO_ROOT_PASSWORD>`)
   - `NGC_API_KEY` (optional: without it the Load NIMs tab cannot read nvcr.io images)
   - optionally `pcai.sso: true` to require a PCAI login
5. **Deploy**. The first start imports the bundled catalog (about a minute, shown in the website header).

Change values later with **Configure** on the application tile; the pods restart with the new Secret.

## Values

| Value | Default | Description |
| --- | --- | --- |
| `ezua.virtualService.endpoint` | `nim-advisor.${DOMAIN_NAME}` | Application endpoint (PCAI fills in the domain) |
| `pcai.enabled` | `true` | VirtualService + labels policy; `false` = plain Kubernetes (use `ingress.*`) |
| `pcai.sso` | `false` | Istio AuthorizationPolicy with the PCAI oauth2-proxy |
| `pcai.kyverno` | `true` | Kyverno ClusterPolicy for the `hpe-ezua` labels |
| `env.MONGO_ROOT_USER` / `env.MONGO_ROOT_PASSWORD` / `env.MONGO_DB` | `nimadmin` / placeholder / `nim_advisor` | MongoDB credentials and database |
| `env.NGC_API_KEY` | placeholder | NGC key used by the Load NIMs tab |
| `env.FETCH_WORKERS` / `env.IMPORT_ENABLED` | `4` / `true` | Parallel fetches; `false` = read-only advisor |
| `env.SEED_IMPORT` | `if-changed` | Import the bundled catalog at start-up: `if-changed`, `always` or `off` |
| `env.INTERNAL_REGISTRY_PREFIX` | `registry.example.com/ezmeral-common/` | Catalog images under this prefix are fetched from nvcr.io |
| `image.repository` / `image.tag` | `ghcr.io/osadsanu/nim-profile-advisor` / `latest` | Image built by the *Container image* workflow; pin `sha-<commit>` or a version |
| `imagePullSecrets` | `[]` | Pull secret when the GHCR package (or your mirror) is private |
| `fetchTmp.sizeLimit` | `8Gi` | Scratch space for downloaded manifest layers |
| `mongodb.image.tag` | `8.0` | Use `7.0` on nodes with Linux 6.19 to 7.0.13 (MongoDB 8 refuses to start there) |
| `mongodb.persistence.size` / `storageClass` | `5Gi` / default class | MongoDB volume |
| `mongodb.enabled` / `mongodb.externalUri` | `true` / empty | Use an existing MongoDB instead |

Any other key added to `env` is passed to the website as an environment variable (e.g. `FETCH_TIMEOUT`).

## Notes

- **Image access**: GHCR packages start private. Make the package public (package settings) or create a pull
  secret in the namespace and list it in `imagePullSecrets`. Air-gapped systems: mirror the image and `mongo:8.0`
  and set `image.repository: "${AIRGAP_REGISTRY}/osadsanu/nim-profile-advisor"` (and `mongodb.image.repository`).
- **MongoDB password**: MongoDB creates the root user when its volume is first initialized. Changing
  `env.MONGO_ROOT_PASSWORD` afterwards does not change it inside MongoDB; change it there too, or delete the PVC
  `data-<release>-nim-profile-advisor-mongodb-0` to start with an empty database.
- **Data**: NIMs loaded from the website are stored in MongoDB (`catalog_additions`) and survive upgrades and
  re-imports. A newer image with newer bundled data is imported automatically on the next start.
- **Kyverno scope**: the PCAI documentation warns that a policy for every pod of a project namespace can break
  platform jobs (`chown-project-pvc`, `ml-pipeline-ui-artifact`). The policy of this chart only matches pods with
  `app.kubernetes.io/instance: <release>`; the labels are also set directly in the pod templates.
- **Uninstall**: Helm does not delete the Kyverno hook policy or the MongoDB volume:
  `kubectl delete clusterpolicy add-vendor-app-labels-<release>-nim-profile-advisor` and delete the PVC to drop the data.
- **Plain Kubernetes**: `helm install nim-advisor helm/nim-profile-advisor --set pcai.enabled=false
  --set env.MONGO_ROOT_PASSWORD=<password>`, then `kubectl port-forward svc/nim-advisor-nim-profile-advisor 8080:80`
  or enable `ingress`.
