# load prototype packages from npm 
# load target labels that correspond to their security vulnerabilities specified in the google sheet

# load libraries 
import requests
import json
from datetime import datetime, timezone, date, timedelta
from semantic_version import Version
import calendar
import os
from semantic_version import NpmSpec

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

headers = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json"
}
# choose prototype packages, inspired from https://www.geeksforgeeks.org/node-js/top-npm-packages-for-node/
# those packages are available in the npm registry
PACKAGES = [
    "lodash",
    # "express",
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

        monthly_releases[f"package_stable_release_count_M{month_index}"] = (
            release_count
        )

    return monthly_releases
def get_github_repository(metadata):

    repository = metadata.get("repository")

    if not repository:
        return None

    if isinstance(repository, dict):
        url = repository.get("url")
    else:
        url = repository

    return url
from urllib.parse import urlparse


def get_month_index(event_date):
    """
    M0 = June 2026
    M1 = May 2026
    ...
    M11 = July 2025
    """
    return (2026 - event_date.year) * 12 + (6 - event_date.month)


def get_month_boundaries():
    """
    Return the exclusive end of each month.
    An issue is open at month end if it is still open
    immediately before the next month's start.
    """
    boundaries = []

    for i in range(12):
        month = 6 - i
        year = 2026

        if month <= 0:
            month += 12
            year -= 1

        if month == 12:
            next_month = 1
            next_year = year + 1
        else:
            next_month = month + 1
            next_year = year

        boundaries.append(
            datetime(
                next_year, next_month, 1,
                tzinfo=timezone.utc
            )
        )

    return boundaries


def get_empty_github_metrics():
    metrics = {}

    metric_names = [
        "issues_opened",
        "issues_closed",
        "issues_reopened",
        "pull_requests_opened",
        "pull_requests_closed_unmerged",
        "pull_requests_merged",
        "pull_requests_reopened",
        "issues_open_at_month_end",
        "pull_requests_open_at_month_end",
    ]

    for name in metric_names:
        for i in range(12):
            metrics[f"{name}_M{i}"] = 0

    return metrics


def get_issue_events(owner, repo, issue_number, headers):
    """
    Retrieve all available historical events for one issue or PR.
    """
    url = (
        f"https://api.github.com/repos/{owner}/{repo}"
        f"/issues/{issue_number}/events"
    )

    events = []
    page = 1

    while True:
        response = requests.get(
            url,
            headers=headers,
            # params={
            #     "per_page": 100,
            #     "page": page,
            # },
            params = {
                "state": "all",
                "per_page": 10,
                "page": 1,
                "sort": "created",
                "direction": "asc",
            },
            timeout=800,
        )
        response.raise_for_status()

        page_events = response.json()
        events.extend(page_events)

        if len(page_events) < 100:
            break

        page += 1

    return events


def get_monthly_github_metrics(owner, repo, headers):
    """
    Calculate monthly issue and pull-request metrics.

    Event counts are based on creation dates and historical
    closed/reopened events.

    Month-end open counts are reconstructed by replaying
    the available historical events.
    """

    print(f"\nFetching GitHub metrics for {owner}/{repo}...")

    metrics = get_empty_github_metrics()
    month_boundaries = get_month_boundaries()

    issues_url = (
        f"https://api.github.com/repos/{owner}/{repo}/issues"
    )

    params = {
        "state": "all",
        "per_page": 100,
        "page": 1,
        "sort": "created",
        "direction": "asc",
    }

    issue_number_checked = 0

    while True:
        print(f"Fetching issue page {params['page']}...")

        response = requests.get(
            issues_url,
            headers=headers,
            params=params,
            timeout=20,
        )
        response.raise_for_status()

        items = response.json()

        if not items:
            break

        for item in items:
            issue_number_checked += 1

            is_pull_request = "pull_request" in item

            created_at = datetime.fromisoformat(
                item["created_at"].replace("Z", "+00:00")
            )

            if created_at >= END_DATE:
                continue

            if created_at < START_DATE:
                created_in_window = False
            else:
                created_in_window = True
                month_index = get_month_index(created_at)

                if 0 <= month_index < 12:
                    if is_pull_request:
                        metrics[
                            f"pull_requests_opened_M{month_index}"
                        ] += 1
                    else:
                        metrics[
                            f"issues_opened_M{month_index}"
                        ] += 1

            print(
                f"Checking {'PR' if is_pull_request else 'issue'} "
                f"#{item['number']} "
                f"({issue_number_checked})..."
            )

            events = get_issue_events(
                owner,
                repo,
                item["number"],
                headers,
            )

            # Store relevant state transitions.
            state_events = []

            for event in events:
                event_type = event.get("event")

                if event_type not in ("closed", "reopened"):
                    continue

                event_date = datetime.fromisoformat(
                    event["created_at"].replace("Z", "+00:00")
                )

                state_events.append((event_date, event_type))

                if not (START_DATE <= event_date < END_DATE):
                    continue

                month_index = get_month_index(event_date)

                if not 0 <= month_index < 12:
                    continue

                if is_pull_request:
                    if event_type == "reopened":
                        metrics[
                            f"pull_requests_reopened_M{month_index}"
                        ] += 1

                    elif event_type == "closed":
                        merged_at = item.get("pull_request", {}).get(
                            "merged_at"
                        )

                        # A closed event at the merge time is a merge,
                        # not an unmerged closure.
                        is_merge_event = False

                        if merged_at:
                            merged_date = datetime.fromisoformat(
                                merged_at.replace("Z", "+00:00")
                            )

                            is_merge_event = (
                                abs((event_date - merged_date).total_seconds())
                                <= 2
                            )

                        if not is_merge_event:
                            metrics[
                                f"pull_requests_closed_unmerged_M{month_index}"
                            ] += 1

                else:
                    if event_type == "closed":
                        metrics[
                            f"issues_closed_M{month_index}"
                        ] += 1

                    elif event_type == "reopened":
                        metrics[
                            f"issues_reopened_M{month_index}"
                        ] += 1

            # Count merged pull requests using merged_at.
            if is_pull_request:
                merged_at = item.get("pull_request", {}).get("merged_at")

                if merged_at:
                    merged_date = datetime.fromisoformat(
                        merged_at.replace("Z", "+00:00")
                    )

                    if START_DATE <= merged_date < END_DATE:
                        month_index = get_month_index(merged_date)

                        if 0 <= month_index < 12:
                            metrics[
                                f"pull_requests_merged_M{month_index}"
                            ] += 1

                    # Ensure a merge closes the PR in the reconstructed state.
                    state_events.append((merged_date, "closed"))

            # Reconstruct the state at each month end.
            # Issues and PRs start in the open state when created.
            state_events.sort(key=lambda event: event[0])

            is_open = True
            event_position = 0

            for month_index, boundary in enumerate(month_boundaries):

                while (
                    event_position < len(state_events)
                    and state_events[event_position][0] < boundary
                ):
                    event_date, event_type = state_events[event_position]

                    if event_type == "closed":
                        is_open = False
                    elif event_type == "reopened":
                        is_open = True

                    event_position += 1

                if created_at < boundary and is_open:
                    if is_pull_request:
                        metrics[
                            f"pull_requests_open_at_month_end_M{month_index}"
                        ] += 1
                    else:
                        metrics[
                            f"issues_open_at_month_end_M{month_index}"
                        ] += 1

        # if len(items) < 100:
        #     break

        # params["page"] += 1
        break

    print(
        f"Finished {owner}/{repo}. "
        f"Checked {issue_number_checked} issues and pull requests."
    )

    return metrics
def get_ghsa_advisories(package_name):
    """
    Retrieve GHSA advisories affecting an npm package.
    Testing mode: maximum 10 advisories.
    """

    url = "https://api.github.com/graphql"

    query = """
    query($package: String!) {
      securityVulnerabilities(
        first: 10,
        ecosystem: NPM,
        package: $package
      ) {
        nodes {
          advisory {
            ghsaId
            publishedAt
            severity
            cvss {
              score
            }
          }
          vulnerableVersionRange
        }
      }
    }
    """

    response = requests.post(
        url,
        headers=headers,
        json={
            "query": query,
            "variables": {
                "package": package_name
            }
        },
        timeout=30,
    )

    response.raise_for_status()

    result = response.json()

    if "errors" in result:
        raise RuntimeError(result["errors"])

    return result["data"]["securityVulnerabilities"]["nodes"]
def get_vulnerability_targets(package_name, latest_version, published_at):
    """
    Count GHSA advisories affecting the latest stable version
    and published within 90 days after its publication.
    """

    targets = {
        "target_ghsa_low_count_90d": 0,
        "target_ghsa_moderate_count_90d": 0,
        "target_ghsa_high_count_90d": 0,
        "target_ghsa_critical_count_90d": 0,
        "target_ghsa_unknown_count_90d": 0,
    }

    start = datetime.fromisoformat(
        published_at.replace("Z", "+00:00")
    )

    end = start + timedelta(days=90)

    advisories = get_ghsa_advisories(package_name)

    counted_ghsas = set()

    for item in advisories:

        advisory = item["advisory"]
        ghsa_id = advisory["ghsaId"]

        # Avoid counting the same advisory twice.
        if ghsa_id in counted_ghsas:
            continue

        advisory_date = datetime.fromisoformat(
            advisory["publishedAt"].replace("Z", "+00:00")
        )

        # Check the 90-day publication window.
        if not (start < advisory_date <= end):
            continue

        # Check whether the latest version is affected.
        vulnerable_range = item["vulnerableVersionRange"]

        try:
            spec = NpmSpec(vulnerable_range)

            if not spec.match(Version(latest_version)):
                continue

        except ValueError:
            print(
                "Could not parse version range:",
                vulnerable_range
            )
            continue

        cvss_score = advisory["cvss"]["score"]

        # Exclude vulnerabilities with CVSS = 0 or unavailable CVSS.
        if cvss_score is None or cvss_score <= 0:
            continue

        severity = (advisory["severity"] or "UNKNOWN").lower()

        if severity == "low":
            targets["target_ghsa_low_count_90d"] += 1
        elif severity == "moderate":
            targets["target_ghsa_moderate_count_90d"] += 1
        elif severity == "high":
            targets["target_ghsa_high_count_90d"] += 1
        elif severity == "critical":
            targets["target_ghsa_critical_count_90d"] += 1
        else:
            targets["target_ghsa_unknown_count_90d"] += 1

        counted_ghsas.add(ghsa_id)

    targets["target_ghsa_total_count_90d"] = sum(
        targets.values()
    )

    targets["target_vulnerable_90d"] = int(
        targets["target_ghsa_total_count_90d"] > 0
    )

    return targets
def get_monthly_ghsa_counts(package_name):
    url = "https://api.github.com/graphql"

    query = """
    query($package: String!) {
      securityVulnerabilities(
        first: 10,
        ecosystem: NPM,
        package: $package
      ) {
        nodes {
          advisory {
            ghsaId
            publishedAt
            severity
          }
        }
      }
    }
    """

    response = requests.post(
        url,
        headers=headers,
        json={
            "query": query,
            "variables": {"package": package_name}
        },
        timeout=30
    )

    response.raise_for_status()
    result = response.json()

    if "errors" in result:
        raise RuntimeError(result["errors"])

    nodes = result["data"]["securityVulnerabilities"]["nodes"]

    # Initialize all monthly counters with zero
    monthly_counts = {}

    severities = [
        "low",
        "moderate",
        "high",
        "critical",
        "unknown"
    ]

    for severity in severities:
        for month_index in range(12):
            monthly_counts[
                f"ghsa_{severity}_count_M{month_index}"
            ] = 0

    # Avoid counting the same GHSA more than once
    seen_advisories = set()

    for node in nodes:
        advisory = node["advisory"]

        ghsa_id = advisory["ghsaId"]

        if ghsa_id in seen_advisories:
            continue

        seen_advisories.add(ghsa_id)

        published_at = advisory["publishedAt"]

        if not published_at:
            continue

        published_date = datetime.fromisoformat(
            published_at.replace("Z", "+00:00")
        )

        # Only consider advisories within the observation period
        if not START_DATE <= published_date < END_DATE:
            continue

        # Calculate month index: M0 = June 2026, M11 = July 2025
        month_index = (
            (END_DATE.year - published_date.year) * 12
            + END_DATE.month - published_date.month
            - 1
        )

        severity = (advisory["severity"] or "UNKNOWN").lower()

        if severity not in severities:
            severity = "unknown"

        key = f"ghsa_{severity}_count_M{month_index}"

        monthly_counts[key] += 1
    # Calculate total counts across all 12 months
    for severity in severities:
        monthly_counts[f"ghsa_{severity}_count"] = sum(
            monthly_counts[f"ghsa_{severity}_count_M{i}"]
            for i in range(12)
        )

    return monthly_counts
def main():
    # Clear previous JSON content
    with open("npm_package_versions.json", "w") as file:
        json.dump([], file)

    all_records = []

    for package in PACKAGES:
        print(f"\nRetrieving {package}...")

        records = extract_package_versions(package)
        print(f"Found {len(records)} stable versions.")

        # Monthly downloads
        monthly_stats = get_monthly_download_stats(package)

        # Package metadata (retrieve once and reuse)
        metadata = get_package_metadata(package)

        # Monthly stable release counts
        monthly_releases = get_monthly_release_counts(metadata)

        # GitHub repository
        repository_url = get_github_repository(metadata)
        print(package, "->", repository_url)

        # Monthly issue closure counts
        # Monthly issue and pull-request metrics
        monthly_github_metrics = {}

        if repository_url:
            # Normalize common GitHub repository URL formats.
            normalized_url = repository_url.replace("git+", "")

            if normalized_url.startswith("git@github.com:"):
                normalized_url = normalized_url.replace(
                    "git@github.com:",
                    "https://github.com/",
                    1,
                )

            parsed_url = urlparse(normalized_url)

            if parsed_url.hostname == "github.com":
                parts = parsed_url.path.strip("/").split("/")

                if len(parts) >= 2:
                    owner = parts[0]
                    repo = parts[1].removesuffix(".git")

                    monthly_github_metrics = get_monthly_github_metrics(
                        owner,
                        repo,
                        headers,
                    )
                else:
                    print("Could not parse GitHub repository:", repository_url)
            else:
                print("Repository is not hosted on GitHub.")
        else:
            print("No repository URL found.")
        ghsa_metrics = get_monthly_ghsa_counts(package)

        # Attach all monthly metrics to every version record.
        for record in records:
            record.update(monthly_stats)
            record.update(monthly_releases)
            record.update(monthly_github_metrics)
            record.update(ghsa_metrics)
        # Identify the latest stable version among collected records.
        
        if records:

            latest_record = max(
                records,
                key=lambda record: Version(
                    record["purl"].split("@")[-1]
                )
            )

            latest_version = latest_record["purl"].split("@")[-1]

            print(
                f"\nChecking vulnerabilities for "
                f"{package}@{latest_version}"
            )

            vulnerability_targets = get_vulnerability_targets(
                package,
                latest_version,
                latest_record["published_at"],
            )

            # Attach target fields ONLY to the latest version record.
            latest_record.update(vulnerability_targets)

            for key, value in vulnerability_targets.items():
                print(f"{key}: {value}")
        all_records.extend(records)

        # Print GitHub metrics.
        for key, value in monthly_github_metrics.items():
            print(f"{key}: {value}")
    # Save all package-version records
    with open("npm_package_versions.json", "w", encoding="utf-8") as file:
        json.dump(all_records, file, indent=4)

    print(f"\nTotal records: {len(all_records)}")
    print("Saved to npm_package_versions.json")

    # Identify the highest stable SemVer among the collected versions
    for package in PACKAGES:
        versions = [
            record["purl"].split("@")[-1]
            for record in all_records
            if record["purl"].startswith(f"pkg:npm/{package}@")
        ]

        if versions:
            latest = max(versions, key=Version)
            print(f"{package}: latest version in the observation window = {latest}")



if __name__ == "__main__":
    main()
