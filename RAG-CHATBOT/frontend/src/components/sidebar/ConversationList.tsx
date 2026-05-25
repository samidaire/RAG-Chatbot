"use client";

import { useApp } from "@/context/AppContext";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Plus, MessageSquare, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { apiService } from "@/lib/api";

export function ConversationList() {
  const {
    conversations,
    currentConversationId,
    setCurrentConversationId,
    setConversations,
  } = useApp();

  const handleNewConversation = () => {
    setCurrentConversationId(null);
  };

  return (
    <div className="p-4">
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-sm text-slate-800 uppercase tracking-wide">
          Conversations
        </h3>
        {/* Only show new conversation button, not close (X) button here */}
        <Button
          variant="ghost"
          size="sm"
          onClick={handleNewConversation}
          className="h-8 w-8 p-0"
        >
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      <ScrollArea className="h-[300px]">
        {conversations.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            <MessageSquare className="h-8 w-8 mx-auto mb-2 opacity-50" />
            <p className="text-sm">No conversations yet.</p>
            <p className="text-xs">Start chatting!</p>
          </div>
        ) : (
          <div className="space-y-2">
            {conversations.map((c) => {
              const selected = currentConversationId === c.conversation_id;
              return (
                <div
                  key={c.conversation_id}
                  className={`flex items-center px-2 py-2 rounded-md border bg-card w-full max-w-full md:max-w-[17rem] lg:max-w-[19rem] overflow-hidden mb-2 md:mb-3 ${
                    selected
                      ? "bg-muted border border-muted-foreground rounded-lg shadow-sm transition-all duration-150"
                      : "hover:bg-muted/60 transition-colors duration-100"
                  }`}
                >
                  <button
                    className="flex-1 text-left pr-2"
                    onClick={() => setCurrentConversationId(c.conversation_id)}
                    aria-label={c.title}
                  >
                    <div className="flex flex-col w-full gap-1">
                      <h4
                        className={`text-base font-semibold truncate max-w-[180px] md:max-w-[150px] ${
                          selected
                            ? "text-foreground font-semibold"
                            : "text-slate-900"
                        }`}
                      >
                        {c.title}
                      </h4>
                      <p className="text-xs text-slate-700 line-clamp-2 max-w-[160px] md:max-w-[140px]">
                        {c.last_message_preview || "No messages yet."}
                      </p>
                    </div>
                  </button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive ml-2"
                    onClick={async () => {
                      try {
                        await apiService.deleteConversation(c.conversation_id);
                        setCurrentConversationId(null);
                        setConversations(
                          conversations.filter(
                            (x) => x.conversation_id !== c.conversation_id
                          )
                        );
                        toast.success("Conversation deleted");
                      } catch (err) {
                        console.error(err);
                        toast.error("Failed to delete conversation");
                      }
                    }}
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              );
            })}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}
