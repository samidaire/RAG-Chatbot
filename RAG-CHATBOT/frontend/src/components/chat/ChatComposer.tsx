"use client";

import { useApp } from "@/context/AppContext";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Send } from "lucide-react";
import { useState, useRef, useEffect, KeyboardEvent } from "react";
import { apiService } from "@/lib/api";
import { ChatRequest } from "@/lib/types";
import { FileText } from "lucide-react";
import {
  Sheet,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";

export function ChatComposer() {
  const {
    addMessage,
    setIsChatting,
    isChatting,
    messages,
    currentConversationId,
    documents,
  } = useApp();
  const [input, setInput] = useState("");
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([]);
  const [sheetOpen, setSheetOpen] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = `${textareaRef.current.scrollHeight}px`;
    }
  }, [input]);

  // Remove deselected docs if they disappear
  useEffect(() => {
    setSelectedDocumentIds((ids) =>
      ids.filter((id) => documents.some((d) => d.document_id === id))
    );
  }, [documents]);

  const handleSubmit = async () => {
    if (!input.trim() || isChatting) return;

    const userMessage = {
      id: `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
      role: "user" as const,
      content: input.trim(),
      timestamp: new Date().toISOString(),
    };

    addMessage(userMessage);
    setInput("");
    setIsChatting(true);

    try {
      const req: ChatRequest = {
        query: userMessage.content,
        conversation_id: currentConversationId || undefined,
        allowed_document_ids:
          selectedDocumentIds.length > 0 ? selectedDocumentIds : undefined,
      };
      const resp = await apiService.sendChatMessage(req);
      let assistantContent = "";
      let citations = undefined;
      if (typeof resp === "string") {
        assistantContent = resp;
      } else if (resp && typeof resp === "object") {
        if ("answer" in resp && typeof resp.answer === "string") {
          assistantContent = resp.answer;
        } else if (resp.message && typeof resp.message === "string") {
          assistantContent = resp.message;
        }
        if (
          "citations" in resp &&
          Array.isArray((resp as { citations?: unknown }).citations)
        ) {
          citations = (
            resp as {
              citations: Array<{
                document_id: string;
                document_name?: string;
                chunk_text?: string;
                source_name?: string;
                snippet?: string;
                score?: number;
              }>;
            }
          ).citations.map((c) => {
            let document_name = c.document_name;
            if (!document_name && typeof c.source_name === "string") {
              document_name = c.source_name;
            }
            let chunk_text = c.chunk_text;
            if (!chunk_text && typeof c.snippet === "string") {
              chunk_text = c.snippet;
            }
            return {
              document_id: c.document_id,
              document_name,
              chunk_text,
              score: typeof c.score === "number" ? c.score : 1,
            };
          });
        }
      }
      if (assistantContent.trim()) {
        addMessage({
          id: `msg_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
          role: "assistant",
          content: assistantContent,
          timestamp: new Date().toISOString(),
          citations,
        });
      }
    } catch (error) {
      console.error(error);
      addMessage({
        id: `msg_err_${Date.now()}`,
        role: "assistant",
        content: "Sorry, I couldn't process your request. Please try again.",
        timestamp: new Date().toISOString(),
      });
    } finally {
      setIsChatting(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const canSend = input.trim().length > 0 && !isChatting;

  return (
    <div className="border-t bg-background p-4">
      <div className="max-w-4xl mx-auto">
        {/* Document selector pill and chips - moved above send button, left aligned, with docs icon */}
        <div className="mb-2">
          <div className="flex items-center gap-2">
            <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
              <SheetTrigger asChild>
                <Button
                  variant="outline"
                  className="rounded-full px-3 py-1 text-sm font-medium shadow flex items-center gap-2"
                  onClick={() => setSheetOpen(true)}
                >
                  <FileText className="w-4 h-4 mr-1" />
                  {selectedDocumentIds.length === 0
                    ? "Documents"
                    : `${selectedDocumentIds.length} Document${
                        selectedDocumentIds.length > 1 ? "s" : ""
                      } Selected`}
                </Button>
              </SheetTrigger>
              <SheetContent side="right" className="p-4">
                <SheetHeader>
                  <SheetTitle>
                    <span className="sr-only">Select Documents</span>
                  </SheetTitle>
                  <div className="font-semibold text-lg mb-2">
                    Select Documents
                  </div>
                </SheetHeader>
                <div className="space-y-2">
                  {documents.length === 0 ? (
                    <div className="text-muted-foreground">
                      No documents available.
                    </div>
                  ) : (
                    documents.map((doc) => (
                      <label
                        key={doc.document_id}
                        className="flex items-center space-x-2 cursor-pointer"
                      >
                        <input
                          type="checkbox"
                          checked={selectedDocumentIds.includes(
                            doc.document_id
                          )}
                          onChange={(e) => {
                            setSelectedDocumentIds((ids) =>
                              e.target.checked
                                ? [...ids, doc.document_id]
                                : ids.filter((id) => id !== doc.document_id)
                            );
                          }}
                          className="accent-primary h-4 w-4 rounded"
                        />
                        <span className="text-sm font-medium truncate max-w-[180px]">
                          {doc.filename}
                        </span>
                      </label>
                    ))
                  )}
                </div>
              </SheetContent>
            </Sheet>
            {selectedDocumentIds.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {selectedDocumentIds.map((id) => {
                  const doc = documents.find((d) => d.document_id === id);
                  return doc ? (
                    <span
                      key={id}
                      className="flex items-center rounded-full px-4 py-1 bg-primary text-primary-foreground text-sm font-semibold shadow gap-1"
                    >
                      {doc.filename}
                      <button
                        type="button"
                        className="ml-2 text-primary-foreground/70 hover:text-destructive focus:outline-none"
                        aria-label={`Remove ${doc.filename}`}
                        onClick={() =>
                          setSelectedDocumentIds((ids) =>
                            ids.filter((docId) => docId !== id)
                          )
                        }
                      >
                        <svg
                          xmlns="http://www.w3.org/2000/svg"
                          viewBox="0 0 20 20"
                          fill="currentColor"
                          className="w-3 h-3"
                        >
                          <path
                            fillRule="evenodd"
                            d="M10 8.586l4.95-4.95a1 1 0 111.414 1.414L11.414 10l4.95 4.95a1 1 0 01-1.414 1.414L10 11.414l-4.95 4.95a1 1 0 01-1.414-1.414L8.586 10l-4.95-4.95A1 1 0 115.05 3.636L10 8.586z"
                            clipRule="evenodd"
                          />
                        </svg>
                      </button>
                    </span>
                  ) : null;
                })}
              </div>
            )}
          </div>
        </div>

        <div className="flex items-end space-x-3">
          <div className="flex-1 relative">
            <Textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask a question about your documents..."
              className="min-h-[44px] max-h-32 resize-none pr-12"
              disabled={isChatting}
            />
          </div>
          <Button
            onClick={handleSubmit}
            disabled={!canSend}
            size="sm"
            className="h-11 w-11 p-0 flex-shrink-0"
          >
            <Send className="h-4 w-4" />
          </Button>
        </div>

        <div className="flex items-center justify-between mt-2 text-xs text-muted-foreground">
          <span>Press Enter to send, Shift+Enter for new line</span>
          {messages.length > 0 && <span>{input.length} characters</span>}
        </div>
      </div>
    </div>
  );
}
