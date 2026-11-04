# Example step of the stand preparation: removes data of earlier tests.
param([ValidateSet('plan', 'apply')][string]$Mode = 'plan', [Parameter(Mandatory)][string]$OutputPath)
@{ mode = $Mode; changed = $false } | ConvertTo-Json | Set-Content $OutputPath -Encoding UTF8
