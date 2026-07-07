"""WorldAI: agentischer Chat über dem Weltmodell (Web-UI unter /ai).

Ruft die MCP-Tools in-process über dieselbe FastMCP-Registry auf, aus der
auch der MCP-Server seine Tools zieht — kein HTTP, keine zweite
Tool-Definition. Schreib-Tools laufen durch dasselbe Verfassungs-Gate.
"""
