#!/bin/bash
set -e

VERSION_FILE="core/__init__.py"
CHANGELOG_FILE="changelog.md"

SOURCE_COMMIT=$(git rev-parse HEAD)
export SOURCE_COMMIT=${SOURCE_COMMIT:0:8}

PROJECT_VERSION=$(cat $VERSION_FILE \
  | grep API_VERSION \
  | head -1 \
  | awk -F= '{ print $2 }' \
  | sed "s/[',]//g" \
  | tr -d '[[:space:]]')

TAG="$PROJECT_VERSION-$SOURCE_COMMIT"

./set_build_version.sh

git checkout -b release
git add $VERSION_FILE
git commit -m "Release version $PROJECT_VERSION"

git tag "$TAG"

git remote set-url origin ${GIT_REPO_URL}
git push origin --tags

docker pull $DOCKER_IMAGE_ID
docker tag $DOCKER_IMAGE_ID $DOCKER_IMAGE_NAME:$TAG
docker push $DOCKER_IMAGE_NAME:$TAG

if [[ "$INCREASE_MAINTENANCE_VERSION" = true ]]; then
  git checkout master

  NEW_PROJECT_VERSION=$(echo "${PROJECT_VERSION}" | awk -F. -v OFS=. '{$NF++;print}')
  sed -i "s/API_VERSION = '$PROJECT_VERSION'/API_VERSION = '$NEW_PROJECT_VERSION'/" $VERSION_FILE

  python release_notes.py $PROJECT_VERSION $NEW_PROJECT_VERSION True | cat - $CHANGELOG_FILE > temp && mv temp $CHANGELOG_FILE

  git add $VERSION_FILE $CHANGELOG_FILE
  git commit -m "[skip ci] Increase maintenance version to $NEW_PROJECT_VERSION"

  git push origin master
fi
