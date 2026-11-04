# Example step of the stand preparation: test accounts of a region.
param([Parameter(Mandatory)][string]$Region, [Parameter(Mandatory)][string]$OutputPath)
@{ region = $Region; changed = $false } | ConvertTo-Json | Set-Content $OutputPath -Encoding UTF8
