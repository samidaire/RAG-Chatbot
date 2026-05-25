"use client";

import { useApp } from "@/context/AppContext";
import { MessageBubble } from "./MessageBubble";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MessageSquare } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export function ChatList() {
  const { messages, isChatting } = useApp();
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const [showScrollButton, setShowScrollButton] = useState(false);

  // Always scroll to bottom on mount and when messages change
  useEffect(() => {
    if (scrollAreaRef.current) {
      const scrollContainer = scrollAreaRef.current.querySelector(
        '[data-slot="scroll-area-viewport"]'
      ) as HTMLElement | null;
      if (scrollContainer) {
        // Scroll to bottom always
        requestAnimationFrame(() => {
          scrollContainer.scrollTo({
            top: scrollContainer.scrollHeight,
            behavior: "auto",
          });
        });
        // Show scroll-to-bottom button if not at bottom
        const threshold = 120;
        const isNearBottom =
          scrollContainer.scrollHeight -
            scrollContainer.scrollTop -
            scrollContainer.clientHeight <
          threshold;
        setShowScrollButton(!isNearBottom);
        // Listen for manual scrolls to show/hide button
        const handleScroll = () => {
          const isNearBottom =
            scrollContainer.scrollHeight -
              scrollContainer.scrollTop -
              scrollContainer.clientHeight <
            threshold;
          setShowScrollButton(!isNearBottom);
        };
        scrollContainer.addEventListener("scroll", handleScroll);
        return () => {
          scrollContainer.removeEventListener("scroll", handleScroll);
        };
      }
    }
  }, [messages.length, isChatting]);

  if (messages.length === 0) {
    return (
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="text-center text-muted-foreground">
          <MessageSquare className="h-12 w-12 mx-auto mb-4 opacity-50" />
          <h3 className="text-lg font-medium mb-2">Welcome to RAG Chatbot!</h3>
          <p className="text-sm max-w-md">
            Upload documents and start asking questions to get AI-powered
            answers with source citations from your documents.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="relative flex-1 h-full min-h-0 bg-gradient-to-b from-gray-100 via-white to-white">
      <ScrollArea
        ref={scrollAreaRef}
        className="flex-1 h-full min-h-0 p-4"
        key={`${messages.length}-${isChatting}`}
      >
        <div className="space-y-4 flex flex-col flex-grow">
          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}
          {isChatting && (
            <div className="flex justify-start">
              <div className="bg-muted rounded-lg p-3 max-w-[80%]">
                <div className="flex space-x-1">
                  <div className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce" />
                  <div
                    className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce"
                    style={{ animationDelay: "0.1s" }}
                  />
                  <div
                    className="w-2 h-2 bg-muted-foreground/50 rounded-full animate-bounce"
                    style={{ animationDelay: "0.2s" }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </ScrollArea>
      {showScrollButton && (
        <button
          className="absolute bottom-6 right-6 z-10 bg-background border border-muted-foreground rounded-full shadow-lg px-3 py-2 flex items-center space-x-2 hover:bg-muted transition"
          onClick={() => {
            if (scrollAreaRef.current) {
              const scrollContainer = scrollAreaRef.current.querySelector(
                '[data-slot="scroll-area-viewport"]'
              ) as HTMLElement | null;
              if (scrollContainer) {
                scrollContainer.scrollTo({
                  top: scrollContainer.scrollHeight,
                  behavior: "smooth",
                });
              }
            }
          }}
          aria-label="Scroll to bottom"
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 20 20"
            fill="none"
            xmlns="http://www.w3.org/2000/svg"
          >
            <path
              d="M10 4V16M10 16L5 11M10 16L15 11"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      )}
    </div>
  );
}
