{{/*
Expand the name of the chart.
*/}}
{{- define "inventory.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "inventory.fullname" -}}
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
{{- define "inventory.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "inventory.labels" -}}
helm.sh/chart: {{ include "inventory.chart" . }}
{{ include "inventory.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "inventory.selectorLabels" -}}
app.kubernetes.io/name: {{ include "inventory.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "inventory.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "inventory.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Return the service location URL
*/}}
{{- define "inventory.serviceLocationUrl" -}}
{{- if .Values.config.serviceLocationUrl }}
{{- .Values.config.serviceLocationUrl }}
{{- else }}
{{- printf "http://%s.%s.svc.cluster.local:%d" (include "inventory.fullname" .) .Release.Namespace (int .Values.service.port) }}
{{- end }}
{{- end }}

{{/*
Return the PVC name
*/}}
{{- define "inventory.pvcName" -}}
{{- if .Values.storage.existingClaim }}
{{- .Values.storage.existingClaim }}
{{- else }}
{{- include "inventory.fullname" . }}-data
{{- end }}
{{- end }}
