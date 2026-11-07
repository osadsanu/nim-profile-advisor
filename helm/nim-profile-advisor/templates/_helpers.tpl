{{/*
Expand the name of the chart.
*/}}
{{- define "nim-profile-advisor.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name (truncated to 63 chars: DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "nim-profile-advisor.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "nim-profile-advisor.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "nim-profile-advisor.labels" -}}
helm.sh/chart: {{ include "nim-profile-advisor.chart" . }}
{{ include "nim-profile-advisor.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels (website)
*/}}
{{- define "nim-profile-advisor.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nim-profile-advisor.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: web
{{- end }}

{{/*
MongoDB: name, labels and selector labels
*/}}
{{- define "nim-profile-advisor.mongodbName" -}}
{{- printf "%s-mongodb" (include "nim-profile-advisor.fullname" . | trunc 55 | trimSuffix "-") }}
{{- end }}

{{- define "nim-profile-advisor.mongodbSelectorLabels" -}}
app.kubernetes.io/name: {{ include "nim-profile-advisor.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: mongodb
{{- end }}

{{- define "nim-profile-advisor.mongodbLabels" -}}
helm.sh/chart: {{ include "nim-profile-advisor.chart" . }}
{{ include "nim-profile-advisor.mongodbSelectorLabels" . }}
app.kubernetes.io/version: {{ .Values.mongodb.image.tag | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "nim-profile-advisor.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "nim-profile-advisor.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Secret with the "env" values (website environment and MongoDB credentials)
*/}}
{{- define "nim-profile-advisor.secretName" -}}
{{- printf "%s-env" (include "nim-profile-advisor.fullname" . | trunc 59 | trimSuffix "-") }}
{{- end }}

{{/*
True when a value is empty or still a <placeholder>
*/}}
{{- define "nim-profile-advisor.isPlaceholder" -}}
{{- $v := toString (default "" .) | trim }}
{{- if or (eq $v "") (regexMatch "^<.*>$" $v) }}true{{ end }}
{{- end }}

{{/*
Connection string of the website: bundled MongoDB or mongodb.externalUri
(user and password escaped like urllib.parse.quote_plus, as pymongo expects)
*/}}
{{- define "nim-profile-advisor.mongoUri" -}}
{{- if .Values.mongodb.enabled }}
{{- if include "nim-profile-advisor.isPlaceholder" .Values.env.MONGO_ROOT_PASSWORD }}
{{- fail "env.MONGO_ROOT_PASSWORD is still a placeholder: set the MongoDB root password (PCAI: Review Config step)" }}
{{- end }}
{{- printf "mongodb://%s:%s@%s:27017/?authSource=admin" (urlquery (toString .Values.env.MONGO_ROOT_USER)) (urlquery (toString .Values.env.MONGO_ROOT_PASSWORD)) (include "nim-profile-advisor.mongodbName" .) }}
{{- else }}
{{- required "mongodb.externalUri is required when mongodb.enabled is false" .Values.mongodb.externalUri }}
{{- end }}
{{- end }}
