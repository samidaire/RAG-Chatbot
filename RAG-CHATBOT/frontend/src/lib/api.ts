import axios, { AxiosInstance, AxiosResponse } from "axios";
import {
  ChatRequest,
  ChatResponse,
  UploadResponse,
  HealthResponse,
  Document,
  Conversation,
} from "./types";
import { toast } from "sonner";

class ApiService {
  private api: AxiosInstance;

  constructor() {
    this.api = axios.create({
      baseURL: process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000",
      timeout: 30000,
      headers: {
        "Content-Type": "application/json",
      },
    });

    // Request interceptor
    this.api.interceptors.request.use(
      (config) => {
        // Add any auth headers here if needed
        return config;
      },
      (error) => {
        return Promise.reject(error);
      }
    );

    // Response interceptor
    this.api.interceptors.response.use(
      (response) => {
        return response;
      },
      (error) => {
        this.handleApiError(error);
        return Promise.reject(error);
      }
    );
  }

  private handleApiError(error: unknown) {
    const axiosError = error as {
      response?: { status: number; data?: unknown };
      request?: unknown;
    };
    if (axiosError.response) {
      // Server responded with error status
      const { status, data } = axiosError.response;
      const message =
        (data as { detail?: string; message?: string })?.detail ||
        (data as { detail?: string; message?: string })?.message ||
        `Request failed with status ${status}`;

      switch (status) {
        case 400:
          toast.error(`Bad Request: ${message}`);
          break;
        case 401:
          toast.error("Authentication required");
          break;
        case 403:
          toast.error("Access forbidden");
          break;
        case 404:
          toast.error("Resource not found");
          break;
        case 422:
          toast.error(`Validation Error: ${message}`);
          break;
        case 500:
          toast.error("Server error. Please try again later.");
          break;
        default:
          toast.error(`Error: ${message}`);
      }
    } else if (axiosError.request) {
      // Network error
      toast.error("Network error. Please check your connection.");
    } else {
      // Other error
      toast.error("An unexpected error occurred");
    }
  }

  // Health Check
  async healthCheck(verbose: boolean = false): Promise<HealthResponse> {
    const response: AxiosResponse<HealthResponse> = await this.api.get(
      `/health${verbose ? "?verbose=1" : ""}`
    );
    return response.data;
  }

  // Document Management
  async uploadDocument(
    file: File,
    autoProcess: boolean = true
  ): Promise<UploadResponse> {
    const formData = new FormData();
    formData.append("files", file);

    const response: AxiosResponse<UploadResponse> = await this.api.post(
      `/documents/upload?auto_process=${autoProcess}`,
      formData,
      {
        headers: {
          "Content-Type": "multipart/form-data",
        },
        timeout: 60000, // 1 minute for file uploads
      }
    );
    return response.data;
  }

  async getDocumentStatus(documentId: string): Promise<UploadResponse> {
    const response: AxiosResponse<UploadResponse> = await this.api.get(
      `/documents/${documentId}/status`
    );
    return response.data;
  }

  async processDocument(documentId: string): Promise<void> {
    await this.api.post(`/documents/${documentId}/process`);
  }

  async getDocuments(): Promise<Document[]> {
    const response: AxiosResponse<{ documents?: Document[] } | Document[]> =
      await this.api.get("/documents");
    if (Array.isArray(response.data)) return response.data;
    if (response.data && Array.isArray(response.data.documents))
      return response.data.documents;
    return [];
  }

  async getConversations(): Promise<Conversation[]> {
    const response: AxiosResponse<
      { conversations?: Conversation[] } | Conversation[]
    > = await this.api.get("/conversations");
    if (Array.isArray(response.data)) return response.data;
    if (response.data && Array.isArray(response.data.conversations))
      return response.data.conversations;
    return [];
  }

  async deleteConversation(conversationId: string): Promise<void> {
    await this.api.delete(`/conversations/${conversationId}`);
  }

  async deleteDocument(documentId: string): Promise<void> {
    await this.api.delete(`/documents/${documentId}`);
  }

  // Chat Operations
  async sendChatMessage(request: ChatRequest): Promise<ChatResponse | string> {
    const response: AxiosResponse<ChatResponse | string> = await this.api.post(
      "/chat",
      request
    );
    return response.data;
  }

  async getChatHistory(
    conversationId: string
  ): Promise<import("./types").Message[]> {
    const response: AxiosResponse<
      import("./types").Message[] | { messages?: import("./types").Message[] }
    > = await this.api.get(`/chat/history?conversation_id=${conversationId}`);
    if (Array.isArray(response.data)) return response.data;
    if (
      response.data &&
      Array.isArray(
        (response.data as { messages?: import("./types").Message[] }).messages
      )
    ) {
      return (response.data as { messages: import("./types").Message[] })
        .messages!;
    }
    return [];
  }

  async clearConversation(conversationId: string): Promise<void> {
    await this.api.delete(`/chat/clear/${conversationId}`);
  }

  async updateConversationDocuments(
    conversationId: string,
    documentIds: string[]
  ): Promise<void> {
    await this.api.patch(`/chat/documents/${conversationId}`, {
      document_ids: documentIds,
    });
  }

  // Retrieval
  async retrieveDocuments(
    query: string,
    documentIds?: string[]
  ): Promise<unknown> {
    const response: AxiosResponse<unknown> = await this.api.post("/retrieve", {
      query,
      document_ids: documentIds,
    });
    return response.data;
  }
}

// Export singleton instance
export const apiService = new ApiService();
export default apiService;
