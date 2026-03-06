# Specific tag: .\build-push.ps1 -Tag "v1.0"

param(
    [string]$Tag = "latest"
)

$Image = "regyl/navidrome-rw"
$FullTag = "${Image}:${Tag}"

Write-Host "Building $FullTag ..."
docker build -t $FullTag .
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Pushing $FullTag to Docker Hub ..."
docker push $FullTag
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Done. Image: $FullTag"
