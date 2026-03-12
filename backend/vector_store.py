from langchain_community.vectorstores import SupabaseVectorStore
from langchain_openai import OpenAIEmbeddings
from supabase import create_client
import os

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

embeddings = OpenAIEmbeddings()

vector_store = SupabaseVectorStore(
    client=supabase,
    embedding=embeddings,
    table_name="query_memory"
)