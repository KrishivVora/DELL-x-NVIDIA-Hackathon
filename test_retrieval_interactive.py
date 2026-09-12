from claimtrace.ingest import ingest_file
from claimtrace.retrieval import retrieve_candidates

ingest_file("demo", "demo-data/structural.pdf")

for r in retrieve_candidates("demo", "proof by induction on trees", top_k=5):
    print(round(r["score"], 3), r["page"], r["text"][:100])
    
'''
After running this file you MUST run the following command to clean up the local mongoDB:
$ mongosh claimtrace --quiet --eval 'db.documents.deleteMany({project_id:"demo"}); db.chunks.deleteMany({project_id:"demo"});'
'''