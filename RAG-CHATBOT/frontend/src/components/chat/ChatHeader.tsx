"use client";

import { useApp } from "@/context/AppContext";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Menu, Trash2, FileText } from "lucide-react";

export function ChatHeader() {
  const {
    sidebarOpen,
    setSidebarOpen,
    currentConversationId,
    conversations,
    documents,
    messages,
  } = useApp();

  const currentDocuments = documents.filter((doc) => doc.status === "ready");
  const currentConversation = conversations.find(
    (conv) => conv.conversation_id === currentConversationId
  );
  const conversationTitle = currentConversation
    ? currentConversation.title
    : "New Conversation";

  return (
    <div className="flex flex-col px-3 py-3 border-b bg-background md:px-4 md:py-4">
      {/* Mobile layout when sidebar is closed */}
      {!sidebarOpen && (
        <div className="flex items-center justify-between md:hidden mb-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sidebar"
          >
            <Menu className="h-5 w-5" />
          </Button>
          {/* Trash icon for mobile, aligned right */}
          {currentConversationId && (
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          )}
        </div>
      )}

      {/* Desktop layout */}
      <div className="flex flex-col md:flex-row md:items-center md:justify-between">
        <div className="flex flex-col md:flex-row md:items-center gap-2 md:gap-4 flex-1">
          {/* Desktop sidebar button (only when sidebar is closed) */}
          {!sidebarOpen && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSidebarOpen(true)}
              className="hidden md:inline-flex"
              aria-label="Open sidebar"
            >
              <Menu className="h-5 w-5" />
            </Button>
          )}

          <div className="flex-1 min-w-0 flex flex-col items-start justify-center">
            <h1 className="font-semibold text-base truncate w-full mt-2 ml-12 md:text-lg md:mb-0">
              {conversationTitle}
            </h1>
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 w-full mt-1 ml-12">
              <div className="flex items-center space-x-1 text-sm text-muted-foreground">
                <FileText className="h-4 w-4" />
                <span>{currentDocuments.length} documents</span>
              </div>
              {messages.length > 0 && (
                <Badge variant="secondary" className="text-xs">
                  {messages.length} messages
                </Badge>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
