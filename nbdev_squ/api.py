# AUTOGENERATED! DO NOT EDIT! File to edit: ../nbs/01_api.ipynb.

# %% auto 0
__all__ = ['logger', 'list_workspaces', 'list_subscriptions', 'list_securityinsights', 'chunks', 'loganalytics_query',
           'query_all', 'atlaskit_transformer']

# %% ../nbs/01_api.ipynb 3
import pandas, json, logging
from .core import *
from diskcache import memoize_stampede
from importlib.resources import path
from subprocess import run
from azure.monitor.query import LogsQueryClient, LogsBatchQuery, LogsQueryStatus
from azure.identity import AzureCliCredential

# %% ../nbs/01_api.ipynb 4
logger = logging.basicConfig(level=logging.WARN)

# %% ../nbs/01_api.ipynb 6
@memoize_stampede(cache, expire=60 * 60 * 3) # cache for 3 hours
def list_workspaces(fmt: str = "df", # df, csv, json, list
                    agency: str = "ALL"): # Agency alias or ALL
    path = datalake_path()
    df = pandas.read_csv((path / "notebooks/lists/SentinelWorkspaces.csv").open())
    df = df.join(pandas.read_csv((path / "notebooks/lists/SecOps Groups.csv").open()).set_index("Alias"), on="SecOps Group", rsuffix="_secops")
    df = df.rename(columns={"SecOps Group": "alias", "Domains and IPs": "domains"})
    df = df.dropna(subset=["customerId"]).sort_values(by="alias")
    if agency != "ALL":
        df = df[df["alias"] == agency]
    if fmt == "df":
        return df
    elif fmt == "csv":
        return df.to_csv()
    elif fmt == "json":
        return df.fillna("").to_dict("records")
    elif fmt == "list":
        return list(df["customerId"].unique())
    else:
        raise ValueError("Invalid format")

# %% ../nbs/01_api.ipynb 9
@memoize_stampede(cache, expire=60 * 60 * 3) # cache for 3 hours
def list_subscriptions():
    return pandas.DataFrame(azcli(["account", "list"]))["id"].unique()

@memoize_stampede(cache, expire=60 * 60 * 3) # cache for 3 hours
def list_securityinsights():
    return pandas.DataFrame(azcli([
        "graph", "query", "--first", "1000", "-q", 
        """
        resources
        | where type =~ 'microsoft.operationsmanagement/solutions'
        | where name startswith 'SecurityInsights'
        | project wlid = tolower(tostring(properties.workspaceResourceId))
        | join kind=leftouter (
            resources | where type =~ 'microsoft.operationalinsights/workspaces' | extend wlid = tolower(id))
            on wlid
        | extend customerId = properties.customerId
        """
    ])["data"])

def chunks(items, size):
    # Yield successive `size` chunks from `items`
    for i in range(0, len(items), size):
        yield items[i:i + size]

@memoize_stampede(cache, expire=60 * 5) # cache for 5 mins
def loganalytics_query(queries: list[str], timespan=pandas.Timedelta("14d")):
    client = LogsQueryClient(AzureCliCredential())
    requests, results = [], []
    for query in queries:
        for workspace_id in list_securityinsights()["customerId"]:
            requests.append(LogsBatchQuery(workspace_id=workspace_id, query=query, timespan=timespan))
    querytime = pandas.Timestamp("now")
    for request_batch in chunks(requests, 100):
        results += client.query_batch(request_batch)
    dfs = []
    for request, result in zip(requests, results):
        if result.status == LogsQueryStatus.PARTIAL:
            table = result.partial_data[0]
            df = pandas.DataFrame(table.rows, columns=table.columns)
        elif result.status == LogsQueryStatus.SUCCESS:
            table = result.tables[0]
            df = pandas.DataFrame(table.rows, columns=table.columns)
        else:
            df = pandas.DataFrame([result.__dict__])
        df["TenantId"] = request.workspace
        df["_query"] = query
        df["_timespan"] = timespan
        df["_querytime"] = querytime
        dfs.append(df)
    return pandas.concat(dfs)

def query_all(query, fmt="df", timespan=pandas.Timedelta("14d")):
    try:
        # Check query is not a plain string and is iterable
        assert not isinstance(query, str)
        iter(query)
    except (AssertionError, TypeError):
        # if it is a plain string or it's not iterable, convert into a list of queries
        query = [query]
    df = loganalytics_query(query, timespan)
    if fmt == "df":
        return df
    elif fmt == "csv":
        return df.to_csv()
    elif fmt == "json":
        return df.fillna("").to_dict("records")
    else:
        raise ValueError("Invalid format")

# %% ../nbs/01_api.ipynb 11
def atlaskit_transformer(inputtext, inputfmt="md", outputfmt="wiki", runtime="node", transformer=path("nbdev_squ", "atlaskit-transformer.bundle.js").absolute()):
    return run([runtime, transformer, inputfmt, outputfmt], input=inputtext, text=True, capture_output=True, check=True).stdout