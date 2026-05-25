export interface Document {
  document_id: string;
  filename: string;
  size: number;
  upload_timestamp: string;
  status: "uploading" | "processing" | "ready" | "error";
  num_chunks?: number;
}

export interface Conversation {
  conversation_id: string;
  title: string;
  created_at: string;
  last_message_at: string;
  document_count: number;
  message_count?: number;
  last_message_preview?: string;
  updated_at?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: string;
  citations?: Citation[];
}

export interface Citation {
  document_id: string;
  document_name?: string;
  chunk_text?: string;
  score?: number;
}

export interface ChatRequest {
  query: string;
  conversation_id?: string;
  allowed_document_ids?: string[];
}

export interface ChatResponse {
  conversation_id: string;
  message: Message;
}

export interface UploadResponse {
  document_id: string;
  filename: string;
  size: number;
  status: string;
}

export interface HealthResponse {
  status: string;
  timestamp: string;
  version?: string;
  database?: {
    status: string;
    collections?: string[];
  };
  vector_store?: {
    status: string;
    index_name?: string;
  };
}
