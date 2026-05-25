# RAG-Chatbot
How it works:
This is a RAG (Retrieval-Augmented Generation) chatbot that lets users upload PDF documents and ask questions about their content.

Usage Steps:
Upload your PDF document through the interface
Wait for the document to be processed (you'll see a "Ready" badge when complete)
Select the document you want to chat about
Start asking questions - the chatbot will answer based on that document's content
Tech Stack:

Frontend: Next.js
Backend: FastAPI (Python)
AI Models & Libraries:
tiktoken for text chunking
text-embedding-3-small for document embeddings
Pinecone for vector database
MongoDB to store metadata (documents, chunks, messages, conversations)
AWS S3 bucket for storing PDFs
The system processes the PDF, creates searchable embeddings, and provides contextual answers based on the document content.

