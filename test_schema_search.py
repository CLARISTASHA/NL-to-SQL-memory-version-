from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import OllamaEmbeddings

embeddings = OllamaEmbeddings(model="phi3:mini")

db = FAISS.load_local(
    "schema_index",
    embeddings,
    allow_dangerous_deserialization=True
)

query = "tasks assigned to user"

results = db.similarity_search(query, k=3)

for i, doc in enumerate(results):
    print(f"\n--- Result {i+1} ---")
    print(doc.page_content)