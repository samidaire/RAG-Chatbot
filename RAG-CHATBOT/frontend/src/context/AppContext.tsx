"use client";

import React, {
  createContext,
  useContext,
  useReducer,
  ReactNode,
  useEffect,
} from "react";
import { apiService } from "@/lib/api";
import { Document, Conversation, Message } from "@/lib/types";

interface AppState {
  // UI State
  sidebarOpen: boolean;

  // Current State
  currentConversationId: string | null;

  // Data Collections
  conversations: Conversation[];
  documents: Document[];
  messages: Message[];

  // Loading States
  isUploading: boolean;
  isProcessing: boolean;
  isChatting: boolean;
}

type AppAction =
  | { type: "SET_SIDEBAR_OPEN"; payload: boolean }
  | { type: "SET_CURRENT_CONVERSATION"; payload: string | null }
  | { type: "SET_CONVERSATIONS"; payload: Conversation[] }
  | { type: "ADD_CONVERSATION"; payload: Conversation }
  | { type: "SET_DOCUMENTS"; payload: Document[] }
  | { type: "ADD_DOCUMENT"; payload: Document }
  | {
      type: "UPDATE_DOCUMENT_STATUS";
      payload: { id: string; status: Document["status"] };
    }
  | { type: "SET_MESSAGES"; payload: Message[] }
  | { type: "ADD_MESSAGE"; payload: Message }
  | { type: "CLEAR_MESSAGES" }
  | { type: "SET_UPLOADING"; payload: boolean }
  | { type: "SET_PROCESSING"; payload: boolean }
  | { type: "SET_CHATTING"; payload: boolean };

const initialState: AppState = {
  sidebarOpen: true,
  currentConversationId: null,
  conversations: [],
  documents: [],
  messages: [],
  isUploading: false,
  isProcessing: false,
  isChatting: false,
};

function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case "SET_SIDEBAR_OPEN":
      return { ...state, sidebarOpen: action.payload };
    case "SET_CURRENT_CONVERSATION":
      return { ...state, currentConversationId: action.payload };
    case "SET_CONVERSATIONS":
      return { ...state, conversations: action.payload };
    case "ADD_CONVERSATION":
      return {
        ...state,
        conversations: [action.payload, ...state.conversations],
      };
    case "SET_DOCUMENTS":
      return { ...state, documents: action.payload };
    case "ADD_DOCUMENT":
      return { ...state, documents: [...state.documents, action.payload] };
    case "UPDATE_DOCUMENT_STATUS":
      return {
        ...state,
        documents: state.documents.map((doc) =>
          doc.document_id === action.payload.id
            ? { ...doc, status: action.payload.status }
            : doc
        ),
      };
    case "SET_MESSAGES":
      return { ...state, messages: action.payload };
    case "ADD_MESSAGE":
      return { ...state, messages: [...state.messages, action.payload] };
    case "CLEAR_MESSAGES":
      return { ...state, messages: [] };
    case "SET_UPLOADING":
      return { ...state, isUploading: action.payload };
    case "SET_PROCESSING":
      return { ...state, isProcessing: action.payload };
    case "SET_CHATTING":
      return { ...state, isChatting: action.payload };
    default:
      return state;
  }
}

interface AppContextType extends AppState {
  // Actions
  setSidebarOpen: (open: boolean) => void;
  setCurrentConversationId: (id: string | null) => void;
  setConversations: (convs: Conversation[]) => void;
  addConversation: (conversation: Conversation) => void;
  setDocuments: (docs: Document[]) => void;
  addDocument: (document: Document) => void;
  updateDocumentStatus: (id: string, status: Document["status"]) => void;
  setMessages: (msgs: Message[]) => void;
  addMessage: (message: Message) => void;
  clearMessages: () => void;
  setIsUploading: (loading: boolean) => void;
  setIsProcessing: (loading: boolean) => void;
  setIsChatting: (loading: boolean) => void;
  removeDocument: (id: string) => void;
}

const AppContext = createContext<AppContextType | undefined>(undefined);

export function AppProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(appReducer, initialState);

  // Load initial documents from backend
  useEffect(() => {
    let mounted = true;
    const load = async () => {
      try {
        const docs = await apiService.getDocuments();
        if (!mounted) return;
        // Normalize response to an array in case the backend returns a wrapped object
        let normalized: unknown[] = [];
        const unknownDocs: unknown = docs;
        if (Array.isArray(unknownDocs)) {
          normalized = unknownDocs as Document[];
        } else if (unknownDocs && typeof unknownDocs === "object") {
          const obj = unknownDocs as Record<string, unknown>;
          if (Array.isArray(obj.documents)) {
            normalized = obj.documents as Document[];
          } else if (Array.isArray(obj.items)) {
            normalized = obj.items as Document[];
          } else if (Array.isArray(obj.data)) {
            normalized = obj.data as Document[];
          } else {
            normalized = [];
          }
        } else {
          normalized = [];
        }
        // Map backend document shape to frontend Document interface
        const mapped = normalized.map((d) => {
          const obj = d as unknown as Record<string, unknown>;
          const document_id = (obj["document_id"] as string) || "";
          const filename =
            (obj["source_name"] as string) ||
            (obj["filename"] as string) ||
            "unnamed.pdf";
          const size =
            typeof obj["size"] === "number" ? (obj["size"] as number) : 0;
          const num_chunks =
            typeof obj["num_chunks"] === "number"
              ? (obj["num_chunks"] as number)
              : undefined;
          const upload_timestamp =
            (obj["created_at"] as string) ||
            (obj["updated_at"] as string) ||
            new Date().toISOString();
          const proc = (obj["processing_status"] as string) || "processing";
          const status =
            proc === "completed"
              ? ("ready" as const)
              : proc === "error"
              ? ("error" as const)
              : ("processing" as const);

          return {
            document_id,
            filename,
            size,
            num_chunks,
            upload_timestamp,
            status,
          } as Document;
        });

        dispatch({ type: "SET_DOCUMENTS", payload: mapped });
      } catch (error) {
        console.error("Failed to load documents", error);
      }
    };
    const loadConversations = async () => {
      try {
        const convs = await apiService.getConversations();
        if (!mounted) return;
        const unknownConvs: unknown = convs;
        let normalizedConvs: unknown[] = [];
        if (Array.isArray(unknownConvs)) normalizedConvs = unknownConvs;
        else if (unknownConvs && typeof unknownConvs === "object") {
          const obj = unknownConvs as Record<string, unknown>;
          if (Array.isArray(obj.conversations))
            normalizedConvs = obj.conversations;
          else if (Array.isArray(obj.items)) normalizedConvs = obj.items;
          else if (Array.isArray(obj.data)) normalizedConvs = obj.data;
        }

        if (normalizedConvs.length === 0) {
          // If nothing normalized, log raw response to help debugging
          console.debug("getConversations returned unexpected shape:", convs);
        }

        const mappedConvs = normalizedConvs.map((c) => {
          const obj = c as unknown as Record<string, unknown>;
          return {
            conversation_id:
              (obj["conversation_id"] as string) || (obj["id"] as string) || "",
            title: (obj["title"] as string) || "Untitled Conversation",
            created_at:
              (obj["created_at"] as string) || new Date().toISOString(),
            last_message_at:
              (obj["last_message_at"] as string) ||
              (obj["updated_at"] as string) ||
              new Date().toISOString(),
            updated_at:
              (obj["updated_at"] as string) ||
              (obj["last_message_at"] as string) ||
              undefined,
            message_count:
              typeof obj["message_count"] === "number"
                ? (obj["message_count"] as number)
                : typeof obj["message_count"] === "string"
                ? parseInt(obj["message_count"] as string, 10) || 0
                : 0,
            last_message_preview:
              (obj["last_message_preview"] as string) ||
              (obj["preview"] as string) ||
              undefined,
            document_count:
              typeof obj["document_count"] === "number"
                ? (obj["document_count"] as number)
                : 0,
          } as Conversation;
        });

        dispatch({ type: "SET_CONVERSATIONS", payload: mappedConvs });
      } catch (error) {
        console.error("Failed to load conversations", error);
      }
    };
    load();
    loadConversations();
    return () => {
      mounted = false;
    };
  }, []);

  // Load messages when conversation changes
  useEffect(() => {
    let mounted = true;
    const loadMessages = async (conversationId: string | null) => {
      if (!conversationId) {
        dispatch({ type: "SET_MESSAGES", payload: [] });
        return;
      }
      try {
        console.debug("Fetching chat history for", conversationId);
        const hist = await apiService.getChatHistory(conversationId);
        console.debug("Raw chat history response:", hist);
        if (!mounted) return;
        const unknownHist: unknown = hist || [];
        let normalized: unknown[] = [];
        if (Array.isArray(unknownHist)) normalized = unknownHist as unknown[];
        else if (unknownHist && typeof unknownHist === "object") {
          const obj = unknownHist as Record<string, unknown>;
          if (Array.isArray(obj.messages))
            normalized = obj.messages as unknown[];
          else if (Array.isArray(obj.data)) normalized = obj.data as unknown[];
          else if (Array.isArray(obj.items))
            normalized = obj.items as unknown[];
          else if (Array.isArray(obj.history))
            normalized = obj.history as unknown[];
        }

        const mapped = normalized.map((m, idx) => {
          const obj = m as unknown as Record<string, unknown>;
          return {
            id:
              (obj["id"] as string) ||
              (obj["message_id"] as string) ||
              `msg_${idx}_${conversationId}`,
            role: ((obj["role"] as string) === "assistant"
              ? "assistant"
              : "user") as "user" | "assistant",
            content:
              (obj["content"] as string) ||
              (obj["text"] as string) ||
              (obj["answer"] as string) ||
              "",
            timestamp:
              (obj["timestamp"] as string) ||
              (obj["created_at"] as string) ||
              new Date().toISOString(),
            citations:
              (obj["citations"] as import("@/lib/types").Citation[]) || [],
          } as Message;
        });
        console.debug("Normalized messages count:", mapped.length);
        if (mapped.length === 0)
          console.debug("No messages normalized from response", hist);
        console.debug(
          "Loaded messages for conversation",
          conversationId,
          "count:",
          mapped.length
        );
        dispatch({ type: "SET_MESSAGES", payload: mapped });
      } catch (error) {
        console.error("Failed to load chat history", error);
        dispatch({ type: "SET_MESSAGES", payload: [] });
      }
    };

    loadMessages(state.currentConversationId);
    return () => {
      mounted = false;
    };
  }, [state.currentConversationId]);

  // Remove document by id
  const removeDocument = (id: string) => {
    dispatch({
      type: "SET_DOCUMENTS",
      payload: state.documents.filter((doc) => doc.document_id !== id),
    });
  };

  return (
    <AppContext.Provider
      value={{
        ...state,
        setSidebarOpen: (open) =>
          dispatch({ type: "SET_SIDEBAR_OPEN", payload: open }),
        setCurrentConversationId: (id) =>
          dispatch({ type: "SET_CURRENT_CONVERSATION", payload: id }),
        setConversations: (convs) =>
          dispatch({ type: "SET_CONVERSATIONS", payload: convs }),
        addConversation: (conv) =>
          dispatch({ type: "ADD_CONVERSATION", payload: conv }),
        setDocuments: (docs) =>
          dispatch({ type: "SET_DOCUMENTS", payload: docs }),
        addDocument: (doc) => dispatch({ type: "ADD_DOCUMENT", payload: doc }),
        updateDocumentStatus: (id, status) =>
          dispatch({ type: "UPDATE_DOCUMENT_STATUS", payload: { id, status } }),
        setMessages: (msgs) =>
          dispatch({ type: "SET_MESSAGES", payload: msgs }),
        addMessage: (msg) => dispatch({ type: "ADD_MESSAGE", payload: msg }),
        clearMessages: () => dispatch({ type: "CLEAR_MESSAGES" }),
        setIsUploading: (loading) =>
          dispatch({ type: "SET_UPLOADING", payload: loading }),
        setIsProcessing: (loading) =>
          dispatch({ type: "SET_PROCESSING", payload: loading }),
        setIsChatting: (loading) =>
          dispatch({ type: "SET_CHATTING", payload: loading }),
        removeDocument,
      }}
    >
      {children}
    </AppContext.Provider>
  );
}

export function useApp() {
  const context = useContext(AppContext);
  if (context === undefined) {
    throw new Error("useApp must be used within an AppProvider");
  }
  return context;
}
