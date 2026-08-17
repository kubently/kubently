{{/*
Expand the name of the chart.
*/}}
{{- define "kubently.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "kubently.fullname" -}}
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
{{- define "kubently.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "kubently.labels" -}}
helm.sh/chart: {{ include "kubently.chart" . }}
{{ include "kubently.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "kubently.selectorLabels" -}}
app.kubernetes.io/name: {{ include "kubently.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "kubently.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "kubently.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Redis URL
*/}}
{{- define "kubently.redisUrl" -}}
{{- if .Values.redis.enabled -}}
redis://{{ .Release.Name }}-redis-master:6379
{{- else if .Values.externalRedis -}}
{{- .Values.externalRedis.url -}}
{{- else -}}
redis://localhost:6379
{{- end -}}
{{- end }}
{{/*
Env var name carrying one external MCP server's bearer token
(referenced as bearer_token_env in the KUBENTLY_MCP_SERVERS JSON).
*/}}
{{- define "kubently.mcpTokenEnvName" -}}
MCP_TOKEN_{{ regexReplaceAll "[^A-Za-z0-9]" . "_" | upper }}
{{- end }}

{{/*
KUBENTLY_MCP_SERVERS JSON from .Values.mcpServers. Tokens themselves never
enter this JSON — entries with existingSecret reference the env var above,
which the deployment fills from the secret.
*/}}
{{- define "kubently.mcpServersJson" -}}
{{- $servers := list -}}
{{- range .Values.mcpServers -}}
{{- $entry := dict "name" .name "url" .url -}}
{{- if .headers -}}{{- $_ := set $entry "headers" .headers -}}{{- end -}}
{{- if .existingSecret -}}{{- $_ := set $entry "bearer_token_env" (include "kubently.mcpTokenEnvName" .name) -}}{{- end -}}
{{- $servers = append $servers $entry -}}
{{- end -}}
{{- toJson $servers -}}
{{- end }}
