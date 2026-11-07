# Build a framework from scratch
This guide will walk you through the steps to build a base framework for nginx server from scratch, including creating the necessary files, configuring the framework, and deploying it to AIE.

You can also check the result in the following repo: [Build-framework](https://github.com/osadsanu/PCAI_frameworks_build)

## Prerequisites
- Access to PCAI system, Admin user credentials.
- Helm installed on your local machine.

## Setting up the environment
Create a folder for your framework and navigate to it:
```bash
mkdir nginx 
cd nginx
helm create nginx
```
> This will create a basic Helm chart for your framework

Add the nginx logo image into the same folder.
At the end, the folder structure should look something like this:
```text
nginx/
├── nginx/
│   ├── charts/
│   ├── templates/
│   ├── .helmignore
│   ├── Chart.yaml
│   └── values.yaml
└── nginx.png
```
If you deploy this framework as is, it will not work in PCAI, because the framework is missing some required labels, virtual services.

If we take a look at the values.yaml file, we can see that the framework is using a default nginx image. We can replace it with our own image, either from a public or private registry.
`values.yaml`
```yaml
# This sets the container image more information can be found here: https://kubernetes.io/docs/concepts/containers/images/
image:
  repository: nginx
  # This sets the pull policy for images.
  pullPolicy: IfNotPresent
  # Overrides the image tag whose default is the chart appVersion.
  tag: ""
```
> To use a private registry you will need to create a secret in the namespace where the framework is deployed, and add the secret name to the values.yaml file.

## Making it work in PCAI:
AIE requires specific networking (a VirtualService) and labels (applied via Kyverno) that the default Helm chart doesn't provide. To make the framework work in PCAI, we need to add 3 important parts and modify the `values.yaml` file:
1. Virtual Service
    1. This makes sure PCAI handles the networking, sets a DNS, routes the traffic to the framework, and makes it accessible from the outside.
2. Kyverno
    - Kyverno is a Kubernetes policy engine that can validate, mutate, or generate resources as they're created.
    - labels are used by the HPE Ezmeral Unified Analytics (EzUA / PCAI) platform to identify and categorize workloads as vendor services and tie Pods to a specific app. Rather than hand-editing every Deployment/Pod template to add these labels, the policy guarantees they're applied consistently to all Pods in the namespace at admission time.
3. Remove `httproute.yaml`
    - We don't need this file; we will use the virtual service to handle the networking and routing.
4. Modify the `values.yaml` file to add the virtual service configuration, container ports, and resources section.
    


### 1. Virtual Service
In the templates folder, create a new file called `virtualservice.yaml` and add the following content:
```yaml
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: {{ printf "%s-%s-virtual-service" .Release.Name .Chart.Name }}
  namespace: {{ .Release.Namespace }}
spec:
  gateways:
    - {{ .Values.ezua.virtualService.istioGateway | required ".Values.ezua.virtualService.istioGateway is required !\n" }}
  hosts:
    - {{ .Values.ezua.virtualService.endpoint | required ".Values.ezua.virtualService.endpoint is required !\n" }}
  http:
    - match:
        - uri:
            prefix: /
      rewrite:
        uri: /
      route:
        - destination:
            host: {{ .Chart.Name }}.{{ .Release.Namespace }}.svc.cluster.local
            port:
              number: {{ .Values.service.port }}
```
### 2. kyverno 
In the templates folder, create a new file called `kyverno.yaml` and add the following content:
```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: {{ printf "add-vendor-app-labels-%s-%s" .Release.Name .Chart.Name }}
  annotations:
    "helm.sh/hook": pre-install
    "helm.sh/hook-weight": "-5"
    "helm.sh/hook-delete-policy": before-hook-creation
spec:
  background: false
  rules:
  - name: add-vendor-app-labels
    match:
      any:
      - resources:
          # list all namespaces defined by the chart here
          # if there are no namespace resource defined leave only .Release.Namespace
          namespaces:
          - {{ .Release.Namespace }}
          kinds:
          - Pod
    mutate:
      patchStrategicMerge:
        metadata:
            labels:
              "hpe-ezua/type": vendor-service
              "hpe-ezua/app": {{ .Chart.Name }}
```
### 3. Remove `httproute.yaml`
We don't need this file; we will use the virtual service to handle the networking and routing.

### 4. Modify the `values.yaml` 

>_Can be on the top of the file_
```yaml
ezua:
  virtualService:
    endpoint: "nginx.${DOMAIN_NAME}"
    istioGateway: "istio-system/ezaf-gateway"
```
### Set containerPorts in the `values.yaml` file:
in the service section, add the `containerPort`, `nodePort`, `labels` and `loadBalancerClass` values, it should look like this:
```yaml
service:
  type: ClusterIP
  port: 80
  #New Values:
  containerPort: 80
  nodePort: ""
  labels: {}
  loadBalancerClass: ""
```
### Modify ingress and remove httpRoute
This modifies how the framework is exposed to the outside. We will remove the httpRoute section and modify the ingress section to use the virtual service configuration we added earlier.
only keep: 
- enabled: false
- className: ""
- annotations: {}

We add:
- host: ""
- additionalHosts: []
- tls: false
- existingSecret: ""


```yaml
ingress:
  enabled: false
  className: ""
  annotations: {}

  host: ""
  additionalHosts: []
  tls: false
  existingSecret: ""

# ❌ DELETE ALL THE FOLLOWING LINES

    # kubernetes.io/ingress.class: nginx
    # kubernetes.io/tls-acme: "true"
  hosts:
    - host: chart-example.local
      paths:
        - path: /
          pathType: ImplementationSpecific
  tls: []
  #  - secretName: chart-example-tls
  #    hosts:
  #      - chart-example.local#
# -- Expose the service via gateway-api HTTPRoute
# Requires Gateway API resources and suitable controller installed within the cluster
# (see: https://gateway-api.sigs.k8s.io/guides/)
httpRoute:
  # HTTPRoute enabled.
  enabled: false
  # HTTPRoute annotations.
  annotations: {}
  # Which Gateways this Route is attached to.
  parentRefs:
  - name: gateway
    sectionName: http
    # namespace: default
  # Hostnames matching HTTP header.
  hostnames:
  - chart-example.local
  # List of rules and filters applied.
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /headers
  #   filters:
  #   - type: RequestHeaderModifier
  #     requestHeaderModifier:
  #       set:
  #       - name: My-Overwrite-Header
  #         value: this-is-the-only-value
  #       remove:
  #       - User-Agent
  # - matches:
  #   - path:
  #       type: PathPrefix
  #       value: /echo
  #     headers:
  #     - name: version
  #       value: v2
```
### Add resources section
Just after the ingress section, add the resources section. It should look like this:
```yaml
resources: 
 limits:
    cpu: 8
    memory: 10Gi
#    nvidia.com/gpu: 1
  requests:
    cpu: 4
    memory: 1Gi
#   nvidia.com/gpu: 1
```

### Remove liveness and readiness
Liveness and readiness probes are not needed for this framework, so we will remove them from the deployment.yaml file. The block to delete looks like this:
```yaml
# ❌ DELETE ALL THE FOLLOWING LINES
livenessProbe:
  httpGet:
    path: /
    port: http
readinessProbe:
  httpGet:
    path: /
    port: http
```

### The final values.yaml file should look like this:
```yaml
# Default values for nginx-static.
# This is a YAML-formatted file.
# Declare variables to be passed into your templates.
ezua:
  virtualService:
    endpoint: "nginx.${DOMAIN_NAME}"
    istioGateway: "istio-system/ezaf-gateway"

annotations: {}
# This will set the replicaset count more information can be found here: https://kubernetes.io/docs/concepts/workloads/controllers/replicaset/
replicaCount: 1

# This sets the container image more information can be found here: https://kubernetes.io/docs/concepts/containers/images/
image:
  repository: nginx
  # This sets the pull policy for images.
  pullPolicy: IfNotPresent
  # Overrides the image tag whose default is the chart appVersion.
  tag: ""

# This is for the secrets for pulling an image from a private repository more information can be found here: https://kubernetes.io/docs/tasks/configure-pod-container/pull-image-private-registry/
imagePullSecrets: []
# This is to override the chart name.
nameOverride: ""
fullnameOverride: ""

# This section builds out the service account more information can be found here: https://kubernetes.io/docs/concepts/security/service-accounts/
serviceAccount:
  # Specifies whether a service account should be created
  create: true
  # Automatically mount a ServiceAccount's API credentials?
  automount: true
  # Annotations to add to the service account
  annotations: {}
  # The name of the service account to use.
  # If not set and create is true, a name is generated using the fullname template
  name: ""

# This is for setting Kubernetes Annotations to a Pod.
# For more information checkout: https://kubernetes.io/docs/concepts/overview/working-with-objects/annotations/
podAnnotations: {}
# This is for setting Kubernetes Labels to a Pod.
# For more information checkout: https://kubernetes.io/docs/concepts/overview/working-with-objects/labels/
podLabels: {}

podSecurityContext: {}
  # fsGroup: 2000

securityContext: {}
  # capabilities:
  #   drop:
  #   - ALL
  # readOnlyRootFilesystem: true
  # runAsNonRoot: true
  # runAsUser: 1000

# This is for setting up a service more information can be found here: https://kubernetes.io/docs/concepts/services-networking/service/
service:
  # This sets the service type more information can be found here: https://kubernetes.io/docs/concepts/services-networking/service/#publishing-services-service-types
  type: ClusterIP
  # This sets the ports more information can be found here: https://kubernetes.io/docs/concepts/services-networking/service/#field-spec-ports
  port: 80
  containerPort: 80
  nodePort: ""
  labels: {}
  loadBalancerClass: ""

# This block is for setting up the ingress for more information can be found here: https://kubernetes.io/docs/concepts/services-networking/ingress/
ingress:
  enabled: false
  className: ""
  annotations: {}
  host: ""
  additionalHosts: []
  tls: false
  existingSecret: ""

resources: 
  # We usually recommend not to specify default resources and to leave this as a conscious
  # choice for the user. This also increases chances charts run on environments with little
  # resources, such as Minikube. If you do want to specify resources, uncomment the following
  # lines, adjust them as necessary, and remove the curly braces after 'resources:'.
  limits:
    cpu: 8
    memory: 10Gi
#    nvidia.com/gpu: 1
  requests:
    cpu: 4
    memory: 1Gi
#   nvidia.com/gpu: 1

# This is to setup the liveness and readiness probes more information can be found here: https://kubernetes.io/docs/tasks/configure-pod-container/configure-liveness-readiness-startup-probes/

# This section is for setting up autoscaling more information can be found here: https://kubernetes.io/docs/concepts/workloads/autoscaling/
autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 100
  targetCPUUtilizationPercentage: 80
  # targetMemoryUtilizationPercentage: 80

# Additional volumes on the output Deployment definition.
volumes: []
# - name: foo
#   secret:
#     secretName: mysecret
#     optional: false

# Additional volumeMounts on the output Deployment definition.
volumeMounts: []
# - name: foo
#   mountPath: "/etc/foo"
#   readOnly: true

nodeSelector: {}

tolerations: []

affinity: {}
```

### Confirm and package
After making all the changes, you can validate and package the framework and import it into PCAI, to do this run the following command:
```bash
# From the chart's parent directory validate it has no helm errors
helm lint nginx/

#package the updated chart
helm package nginx/
```
Upload the `nginx-<version>.tgz` file (the version comes from the `version:` field in `Chart.yaml`) to PCAI through the Import Framework UI.

Check the result in the following repo: [Build-framework](https://github.com/osadsanu/PCAI_frameworks_build/tree/master/nginx) 