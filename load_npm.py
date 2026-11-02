# load prototype packages from npm 
# load target labels that correspond to their security vulnerabilities specified in the google sheet

# load libraries 
import requests
import json
from datetime import datetime, timezone, date
from semantic_version import Version
import calendar


# choose prototype packages, inspired from https://www.geeksforgeeks.org/node-js/top-npm-packages-for-node/
# those packages are available in the npm registry
PACKAGES = [
    "lodash",
    "express",
    # "axios",
    # "react",
    # "minimist",
]
# input is a time series of package data version over the time period: 07.2025-06-2026
START_DATE = datetime(2025, 7, 1, tzinfo=timezone.utc)
END_DATE = datetime(2026, 7, 1, tzinfo=timezone.utc)

# helper functions needed for building the db
# get meta data
def get_package_metadata(package_name):
    url = f"https://registry.npmjs.org/{package_name}"

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    return response.json()
# exclude pre-prelease versions
def is_stable_version(version):
    try:
        parsed = Version(version)
        return not parsed.prerelease and not parsed.build
    except ValueError:
        return False

def count_dependencies(version_data, field):
    return len(version_data.get(field) or {})
# the number of monthly download is package-wise 
# it is a provided by summing up all the number of daily downloads for each month
# example of loadash: https://api.npmjs.org/downloads/range/2026-06-01:2026-06-30/lodash
def get_monthly_download_stats(package_name):
    """
    Retrieve daily npm downloads and aggregate them
    into the 12 monthly periods required by the schema.
    """

    url = (
        f"https://api.npmjs.org/downloads/range/"
        f"2025-07-01:2026-06-30/{package_name}"
    )

    response = requests.get(url, timeout=30)
    response.raise_for_status()

    data = response.json()

    # Map each observed date to its download count.
    daily_downloads = {
        item["day"]: item["downloads"]
        for item in data["downloads"]
        if item.get("day") is not None
        and item.get("downloads") is not None
    }

    monthly_stats = {}

    # M0 = June 2026, M11 = July 2025
    for month_index in range(12):

        year = 2026 - (month_index + 6 <= 0)
        month = 6 - month_index

        if month <= 0:
            month += 12
            year = 2025

        days_in_month = calendar.monthrange(year, month)[1]

        observed_values = []

        for day in range(1, days_in_month + 1):

            current_date = date(year, month, day).isoformat()

            if current_date in daily_downloads:
                observed_values.append(
                    daily_downloads[current_date]
                )

        suffix = f"M{month_index}"

        if observed_values:
            monthly_stats[
                f"package_downloads_{suffix}"
            ] = sum(observed_values)

            monthly_stats[
                f"package_download_days_observed_{suffix}"
            ] = len(observed_values)

        else:
            monthly_stats[
                f"package_downloads_{suffix}"
            ] = None

            monthly_stats[
                f"package_download_days_observed_{suffix}"
            ] = None

    return monthly_stats

def extract_package_versions(package_name):
    metadata = get_package_metadata(package_name)

    package_created_at = metadata["time"].get("created")

    records = []

    for version, version_data in metadata["versions"].items():

        if not is_stable_version(version):
            continue

        published_at = metadata["time"].get(version)

        if published_at is None:
            continue

        published_date = datetime.fromisoformat(
            published_at.replace("Z", "+00:00")
        )

        if not (START_DATE <= published_date < END_DATE):
            continue

        dist = version_data.get("dist") or {}

        record = {
            "purl": f"pkg:npm/{package_name}@{version}",
            "package_created_at": package_created_at,
            "published_at": published_at,

            "unpacked_size": dist.get("unpackedSize"),
            "file_count": dist.get("fileCount"),

            "dependency_count": count_dependencies(
                version_data, "dependencies"
            ),
            "optional_dependency_count": count_dependencies(
                version_data, "optionalDependencies"
            ),
            "peer_dependency_count": count_dependencies(
                version_data, "peerDependencies"
            ),
            "dev_dependency_count": count_dependencies(
                version_data, "devDependencies"
            ),

            "bundled_dependency_count": len(set(
                version_data.get("bundleDependencies")
                or version_data.get("bundledDependencies")
                or []
            )),
        }
        print(
            package_name,
            version,
            published_at,
            "Dependencies:",
            record["dependency_count"]
        )
        records.append(record)
    
    return records
# How many new stable versions of a package were published in each month
def get_monthly_release_counts(metadata):
    monthly_releases = {}

    for month_index in range(12):

        month = 6 - month_index
        year = 2026

        if month <= 0:
            month += 12
            year -= 1

        release_count = 0
        ## 
        for version, published_at in metadata["time"].items():
            # created and modified are metadata entries, not version numbers.
            if version in ("created", "modified"):
                continue
            # exclude non-stable versions and preleased
            if not is_stable_version(version):
                continue

            if published_at is None:
                continue

            published_date = datetime.fromisoformat(
                published_at.replace("Z", "+00:00")
            )

            if (
                published_date.year == year
                and published_date.month == month
            ):
                release_count += 1

        monthly_releases[f"package_releases_M{month_index}"] = (
            release_count
        )

    return monthly_releases

def main():
    all_records = []

    for package in PACKAGES:
        print(f"Retrieving {package}...")

        records = extract_package_versions(package)

        print(f"Found {len(records)} stable versions.")

        all_records.extend(records)
        # monthly downloads
        monthly_stats = get_monthly_download_stats(package)

        for record in records:
            record.update(monthly_stats)
        metadata = get_package_metadata(package)

        monthly_releases = get_monthly_release_counts(metadata)

        for record in records:
            record.update(monthly_releases)
    for month in range(12):
        suffix = f"M{month}"

        downloads = monthly_stats[f"package_downloads_{suffix}"]
        observed_days = monthly_stats[
            f"package_download_days_observed_{suffix}"
        ]

        print(
            f"{suffix}: downloads={downloads}, "
            f"observed_days={observed_days}"
        )
    for key, value in monthly_releases.items():
        print(f"{key}: {value}")
    with open("npm_package_versions.json", "w") as file:
        json.dump(all_records, file, indent=4)

    print(f"\nTotal records: {len(all_records)}")
    print("Saved to npm_package_versions.json")
    for package in PACKAGES:
        versions = [
            record["purl"].split("@")[-1]
            for record in all_records
            if record["purl"].startswith(f"pkg:npm/{package}@")
        ]

        if versions:
            latest = max(versions, key=Version)
            print(f"{package}: latest version = {latest}")

if __name__ == "__main__":
    main()
