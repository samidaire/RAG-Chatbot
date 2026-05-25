"use client";

import { useApp } from "@/context/AppContext";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ChatHeader } from "@/components/chat/ChatHeader";
import { ChatList } from "@/components/chat/ChatList";
import { ChatComposer } from "@/components/chat/ChatComposer";

export function MainLayout() {
  const { sidebarOpen } = useApp();

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar */}
      <Sidebar />

      {/* Main Chat Area */}
      <div
        className={`flex flex-col flex-1 transition-all duration-300 ${
          sidebarOpen ? "ml-0" : "ml-0"
        }`}
      >
        {/* Chat Header */}
        <ChatHeader />

        {/* Chat Messages Area */}
        <div className="flex-1 overflow-hidden">
          <ChatList />
        </div>

        {/* Chat Input */}
        <ChatComposer />
      </div>
    </div>
  );
}
