using System;
using System.Collections.Generic;
using System.Text;

namespace BrBrDbTelemetry
{
    public class DriverNameOverride
    {
        public string car_name_regexp { get; set; }
        public string driver_name { get; set; }

        public DriverNameOverride()
        {
            car_name_regexp = "";
            driver_name = "";
        }
    }

    public class SinkConfig
    {
        public string type { get; set; }
        public bool enabled { get; set; }
        public string path { get; set; }
        public string server_address { get; set; }
        public string api_key { get; set; }

        public SinkConfig()
        {
            type = "";
            enabled = true;
            path = "";
            server_address = "";
            api_key = "";
        }
    }

    public class PluginConfig
    {
        public string car_name_regexp { get; set; }
        public string kart_number_filter { get; set; }
        public string league { get; set; }
        public List<DriverNameOverride> driver_name_overrides { get; set; }
        public List<SinkConfig> sinks { get; set; }

        public PluginConfig()
        {
            car_name_regexp = "KSP.*";
            kart_number_filter = "";
            league = "kartsim";
            driver_name_overrides = new List<DriverNameOverride>();
            sinks = new List<SinkConfig>();
        }
    }

    public static class ConfigHelper
    {
        // Simple JSON Serializer
        public static string Serialize(PluginConfig config)
        {
            var sb = new StringBuilder();
            sb.AppendLine("{");
            sb.AppendLine("    \"car_name_regexp\": " + EscapeString(config.car_name_regexp) + ",");
            sb.AppendLine("    \"kart_number_filter\": " + EscapeString(config.kart_number_filter) + ",");
            sb.AppendLine("    \"league\": " + EscapeString(config.league) + ",");

            // Sinks
            sb.AppendLine("    \"sinks\": [");
            for (int i = 0; i < config.sinks.Count; i++)
            {
                var sink = config.sinks[i];
                sb.AppendLine("        {");
                sb.AppendLine("            \"type\": " + EscapeString(sink.type) + ",");
                sb.AppendLine("            \"enabled\": " + sink.enabled.ToString().ToLower());
                if (sink.type == "file")
                {
                    sb.AppendLine(",\n            \"path\": " + EscapeString(sink.path));
                }
                else if (sink.type == "http")
                {
                    sb.AppendLine(",\n            \"server_address\": " + EscapeString(sink.server_address) + ",");
                    sb.AppendLine("            \"api_key\": " + EscapeString(sink.api_key));
                }
                sb.Append("        }");
                if (i < config.sinks.Count - 1) sb.AppendLine(",");
                else sb.AppendLine();
            }
            sb.AppendLine("    ],");

            // Driver Name Overrides
            sb.AppendLine("    \"driver_name_overrides\": [");
            for (int i = 0; i < config.driver_name_overrides.Count; i++)
            {
                var over = config.driver_name_overrides[i];
                sb.AppendLine("        {");
                sb.AppendLine("            \"car_name_regexp\": " + EscapeString(over.car_name_regexp) + ",");
                sb.AppendLine("            \"driver_name\": " + EscapeString(over.driver_name));
                sb.Append("        }");
                if (i < config.driver_name_overrides.Count - 1) sb.AppendLine(",");
                else sb.AppendLine();
            }
            sb.AppendLine("    ]");

            sb.Append("}");
            return sb.ToString();
        }

        private static string EscapeString(string s)
        {
            if (s == null) return "null";
            return "\"" + s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\n", "\\n").Replace("\r", "\\r") + "\"";
        }

        // Simple custom state-machine JSON Parser tailored for our exact schema
        public static PluginConfig Deserialize(string json)
        {
            var config = new PluginConfig();
            try
            {
                int index = 0;
                var root = ParseValue(json, ref index);
                var dict = root as Dictionary<string, object>;
                if (dict != null)
                {
                    if (dict.ContainsKey("car_name_regexp") && dict["car_name_regexp"] is string)
                        config.car_name_regexp = (string)dict["car_name_regexp"];
                    if (dict.ContainsKey("kart_number_filter") && dict["kart_number_filter"] is string)
                        config.kart_number_filter = (string)dict["kart_number_filter"];
                    if (dict.ContainsKey("league") && dict["league"] is string)
                        config.league = (string)dict["league"];

                    if (dict.ContainsKey("sinks"))
                    {
                        var sinksList = dict["sinks"] as List<object>;
                        if (sinksList != null)
                        {
                            config.sinks.Clear();
                            foreach (var item in sinksList)
                            {
                                var sinkDict = item as Dictionary<string, object>;
                                if (sinkDict != null)
                                {
                                    var sink = new SinkConfig();
                                    if (sinkDict.ContainsKey("type") && sinkDict["type"] is string)
                                        sink.type = (string)sinkDict["type"];
                                    if (sinkDict.ContainsKey("enabled") && sinkDict["enabled"] is bool)
                                        sink.enabled = (bool)sinkDict["enabled"];
                                    if (sinkDict.ContainsKey("path") && sinkDict["path"] is string)
                                        sink.path = (string)sinkDict["path"];
                                    if (sinkDict.ContainsKey("server_address") && sinkDict["server_address"] is string)
                                        sink.server_address = (string)sinkDict["server_address"];
                                    if (sinkDict.ContainsKey("api_key") && sinkDict["api_key"] is string)
                                        sink.api_key = (string)sinkDict["api_key"];

                                    config.sinks.Add(sink);
                                }
                            }
                        }
                    }

                    if (dict.ContainsKey("driver_name_overrides"))
                    {
                        var overridesList = dict["driver_name_overrides"] as List<object>;
                        if (overridesList != null)
                        {
                            config.driver_name_overrides.Clear();
                            foreach (var item in overridesList)
                            {
                                var overDict = item as Dictionary<string, object>;
                                if (overDict != null)
                                {
                                    var over = new DriverNameOverride();
                                    if (overDict.ContainsKey("car_name_regexp") && overDict["car_name_regexp"] is string)
                                        over.car_name_regexp = (string)overDict["car_name_regexp"];
                                    if (overDict.ContainsKey("driver_name") && overDict["driver_name"] is string)
                                        over.driver_name = (string)overDict["driver_name"];

                                    config.driver_name_overrides.Add(over);
                                }
                            }
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                // Fallback to default config on parse error
                Console.WriteLine("JSON Parsing failed, using defaults. Error: " + ex.Message);
            }
            return config;
        }

        private static object ParseValue(string json, ref int index)
        {
            SkipWhitespace(json, ref index);
            if (index >= json.Length) return null;

            char c = json[index];
            if (c == '"')
            {
                return ParseString(json, ref index);
            }
            else if (c == '{')
            {
                return ParseObject(json, ref index);
            }
            else if (c == '[')
            {
                return ParseArray(json, ref index);
            }
            else if (char.IsDigit(c) || c == '-')
            {
                return ParseNumber(json, ref index);
            }
            else if (c == 't' || c == 'f')
            {
                return ParseBool(json, ref index);
            }
            else if (c == 'n')
            {
                index += 4; // null
                return null;
            }
            throw new Exception("Unexpected token: " + c);
        }

        private static void SkipWhitespace(string json, ref int index)
        {
            while (index < json.Length && char.IsWhiteSpace(json[index]))
            {
                index++;
            }
        }

        private static string ParseString(string json, ref int index)
        {
            index++; // skip start quote
            var sb = new StringBuilder();
            while (index < json.Length)
            {
                char c = json[index];
                if (c == '"')
                {
                    index++; // skip end quote
                    return sb.ToString();
                }
                else if (c == '\\')
                {
                    index++;
                    if (index >= json.Length) throw new Exception("Unterminated escape sequence");
                    char esc = json[index];
                    if (esc == '"') sb.Append('"');
                    else if (esc == '\\') sb.Append('\\');
                    else if (esc == '/') sb.Append('/');
                    else if (esc == 'b') sb.Append('\b');
                    else if (esc == 'f') sb.Append('\f');
                    else if (esc == 'n') sb.Append('\n');
                    else if (esc == 'r') sb.Append('\r');
                    else if (esc == 't') sb.Append('\t');
                    else sb.Append(esc);
                }
                else
                {
                    sb.Append(c);
                }
                index++;
            }
            throw new Exception("Unterminated string");
        }

        private static Dictionary<string, object> ParseObject(string json, ref int index)
        {
            index++; // skip '{'
            var dict = new Dictionary<string, object>();
            SkipWhitespace(json, ref index);
            if (index < json.Length && json[index] == '}')
            {
                index++; // empty object
                return dict;
            }

            while (index < json.Length)
            {
                SkipWhitespace(json, ref index);
                if (json[index] != '"') throw new Exception("Expected string key in object, got " + json[index]);
                string key = ParseString(json, ref index);

                SkipWhitespace(json, ref index);
                if (json[index] != ':') throw new Exception("Expected ':' after key in object");
                index++; // skip ':'

                object val = ParseValue(json, ref index);
                dict[key] = val;

                SkipWhitespace(json, ref index);
                if (json[index] == '}')
                {
                    index++;
                    return dict;
                }
                else if (json[index] == ',')
                {
                    index++;
                }
                else
                {
                    throw new Exception("Expected ',' or '}' in object, got " + json[index]);
                }
            }
            throw new Exception("Unterminated object");
        }

        private static List<object> ParseArray(string json, ref int index)
        {
            index++; // skip '['
            var list = new List<object>();
            SkipWhitespace(json, ref index);
            if (index < json.Length && json[index] == ']')
            {
                index++; // empty array
                return list;
            }

            while (index < json.Length)
            {
                object val = ParseValue(json, ref index);
                list.Add(val);

                SkipWhitespace(json, ref index);
                if (json[index] == ']')
                {
                    index++;
                    return list;
                }
                else if (json[index] == ',')
                {
                    index++;
                }
                else
                {
                    throw new Exception("Expected ',' or ']' in array, got " + json[index]);
                }
            }
            throw new Exception("Unterminated array");
        }

        private static bool ParseBool(string json, ref int index)
        {
            if (json[index] == 't')
            {
                index += 4; // true
                return true;
            }
            else
            {
                index += 5; // false
                return false;
            }
        }

        private static double ParseNumber(string json, ref int index)
        {
            int start = index;
            if (json[index] == '-') index++;
            while (index < json.Length && (char.IsDigit(json[index]) || json[index] == '.' || json[index] == 'e' || json[index] == 'E' || json[index] == '+' || json[index] == '-'))
            {
                index++;
            }
            string numStr = json.Substring(start, index - start);
            return double.Parse(numStr);
        }
    }
}
