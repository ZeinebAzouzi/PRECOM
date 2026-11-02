import pandas as pd
import json

with open("npm_package_versions.json", "r") as file:
    data = json.load(file)

df = pd.DataFrame(data)

# Display the table
print(df.to_string())