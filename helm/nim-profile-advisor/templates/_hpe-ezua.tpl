{{/*
HPE EZUA labels (HPE AI Essentials: application health, resource usage and metering).
The EzApp controller checks the health of imported frameworks through pods with hpe-ezua/app=<release>
and hpe-ezua/type=vendor-service.
*/}}
{{- define "hpe-ezua.labels" -}}
hpe-ezua/app: {{ .Release.Name }}
hpe-ezua/type: vendor-service
{{- end }}
