from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import OllamaEmbeddings
from langchain_community.vectorstores import FAISS


loader = TextLoader("schema_full.txt", encoding="utf-8")
documents = loader.load()

print(f"Loaded {len(documents)} schema document")

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=800,
    chunk_overlap=100,
    separators=["\n\n", "\n", " ", ""]
)

docs = text_splitter.split_documents(documents)

print(f"Total chunks created: {len(docs)}")

embeddings = OllamaEmbeddings(model="nomic-embed-text")

vectorstore = FAISS.from_documents(docs, embeddings)

vectorstore.save_local("schema_index")

print("Schema vector store created successfully from schema_full.txt!")