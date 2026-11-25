{{/* INF-05 helpers */}}
{{- define "hrscreen.name" -}}{{ .Chart.Name }}{{- end -}}

{{- define "hrscreen.fullname" -}}
{{- if contains .Chart.Name .Release.Name -}}{{ .Release.Name | trunc 50 | trimSuffix "-" }}
{{- else -}}{{ printf "%s-%s" .Release.Name .Chart.Name | trunc 50 | trimSuffix "-" }}{{- end -}}
{{- end -}}

{{- define "hrscreen.labels" -}}
app.kubernetes.io/name: {{ include "hrscreen.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ include "hrscreen.version" . | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{- define "hrscreen.selector" -}}
app.kubernetes.io/name: {{ include "hrscreen.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* Image by digest (CD-01) or, for local tests only, by tag. */}}
{{- define "hrscreen.image" -}}
{{- if .Values.image.digest -}}
{{ .Values.image.repository }}@{{ .Values.image.digest }}
{{- else if .Values.image.tag -}}
{{ .Values.image.repository }}:{{ .Values.image.tag }}
{{- else -}}
{{ fail "set image.digest (sha256:... from the build.yml run summary) or, for tests, image.tag" }}
{{- end -}}
{{- end -}}

{{- define "hrscreen.version" -}}
{{- if .Values.image.digest -}}{{ .Values.image.digest | trimPrefix "sha256:" | trunc 12 }}
{{- else -}}{{ .Values.image.tag | default .Chart.AppVersion | trunc 63 }}{{- end -}}
{{- end -}}

{{/* PSS "restricted": non-root, no privilege escalation, no capabilities, seccomp, read-only root. */}}
{{- define "hrscreen.podSecurity" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{- define "hrscreen.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
privileged: false
capabilities:
  drop: ["ALL"]
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Settings as env entries (for the migration hook, which runs before the ConfigMap exists). */}}
{{- define "hrscreen.configEnv" -}}
{{- range $k, $v := .Values.config }}
- name: {{ $k }}
  value: {{ $v | toString | quote }}
{{- end }}
{{- end -}}

{{- define "hrscreen.pod" -}}
{{- /* the migration hook runs before the chart's ServiceAccount exists: it uses "default" */}}
serviceAccountName: {{ .serviceAccount | default (include "hrscreen.fullname" .root) }}
automountServiceAccountToken: false
enableServiceLinks: false
securityContext:
  {{- include "hrscreen.podSecurity" .root | nindent 2 }}
{{- with .root.Values.imagePullSecrets }}
imagePullSecrets:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .root.Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .root.Values.tolerations }}
tolerations:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- with .root.Values.affinity }}
affinity:
  {{- toYaml . | nindent 2 }}
{{- end }}
volumes:
  - name: tmp
    emptyDir:
      sizeLimit: {{ .tmpSize | default "256Mi" }}
{{- end -}}
