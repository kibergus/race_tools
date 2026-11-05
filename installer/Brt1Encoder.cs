using System;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Globalization;
using System.Buffers.Binary;
using System.Collections.Generic;

namespace BrBrDbTelemetry
{
    public class Brt1Channel
    {
        public string Name { get; set; }
        public double Scale { get; set; }

        public Brt1Channel(string name, double scale)
        {
            Name = name;
            Scale = scale <= 0 ? 0.001 : scale;
        }
    }

    public static class Brt1Encoder
    {
        public const byte TypeMagic = 0x01;
        public const byte TypeMetadataJson = 0x02;
        public const byte TypeBaseline = 0x03;
        public const byte TypeDelta = 0x04;
        public const byte TypeEndOfStream = 0xFF;

        public const short NanDelta = -32768; // 0x8000

        public static double GetDefaultChannelScale(string channelName)
        {
            if (string.IsNullOrEmpty(channelName)) return 0.001;
            string name = channelName.Trim();

            switch (name)
            {
                case "Time": return 0.001;
                case "x":
                case "y":
                case "z": return 0.001;
                case "Latitude":
                case "Longitude": return 1e-7;
                case "Speed": return 0.01;
                case "GForceLat":
                case "GForceLon":
                case "GForceVert": return 0.001;
                case "Throttle":
                case "Brake":
                case "Clutch":
                case "Handbrake": return 0.1;
                case "Steering Angle":
                case "Steering": return 0.05;
                case "Gear":
                case "Lap":
                case "Flag": return 1.0;
                case "Raining":
                case "Avg Track Wetness": return 0.001;
                case "Track Temp":
                case "Ambient Temp": return 0.01;
            }

            if (name.StartsWith("Toe ", StringComparison.OrdinalIgnoreCase)) return 0.0001;
            if (name.StartsWith("Ori Quat ", StringComparison.OrdinalIgnoreCase)) return 0.0001;
            if (name.StartsWith("RPS ", StringComparison.OrdinalIgnoreCase)) return 0.01;
            if (name.Contains("Patch Vel", StringComparison.OrdinalIgnoreCase)) return 0.01;
            if (name.Contains("Tyre Load", StringComparison.OrdinalIgnoreCase) || name.Contains("Tire Load", StringComparison.OrdinalIgnoreCase)) return 0.1;
            if (name.Contains("Force", StringComparison.OrdinalIgnoreCase)) return 0.1;
            if (name.Contains("Slide Pct", StringComparison.OrdinalIgnoreCase)) return 0.1;
            if (name.Contains("Temp", StringComparison.OrdinalIgnoreCase)) return 0.01;

            return 0.001;
        }

        public static void WriteChunk(Stream output, byte type, byte[] payload)
        {
            ushort len = (ushort)(payload != null ? payload.Length : 0);
            output.WriteByte((byte)(len & 0xFF));
            output.WriteByte((byte)((len >> 8) & 0xFF));
            output.WriteByte(type);
            if (payload != null && len > 0)
            {
                output.Write(payload, 0, len);
            }
        }

        public static void WriteMagicChunk(Stream output)
        {
            byte[] magic = Encoding.ASCII.GetBytes("BRT1");
            WriteChunk(output, TypeMagic, magic);
        }

        public static void WriteMetadataChunk(Stream output, Dictionary<string, string> metadata, List<Brt1Channel> channels)
        {
            string trackName = metadata.GetValueOrDefault("Track name", metadata.GetValueOrDefault("track_name", "Unknown Track"));
            string trackConfig = metadata.GetValueOrDefault("Track configuration", metadata.GetValueOrDefault("track_configuration", ""));
            string date = metadata.GetValueOrDefault("Date", metadata.GetValueOrDefault("date", ""));
            string time = metadata.GetValueOrDefault("Time", metadata.GetValueOrDefault("time", ""));
            string driverName = metadata.GetValueOrDefault("Driver name", metadata.GetValueOrDefault("driver_name", "Unknown Driver"));
            string league = metadata.GetValueOrDefault("League", metadata.GetValueOrDefault("league", "kartsim"));
            string className = metadata.GetValueOrDefault("Class", metadata.GetValueOrDefault("class", metadata.GetValueOrDefault("class_name", "Unknown Class")));
            string session = metadata.GetValueOrDefault("Session", metadata.GetValueOrDefault("session", metadata.GetValueOrDefault("session_name", "Practice")));
            string kartNumber = metadata.GetValueOrDefault("Kart number", metadata.GetValueOrDefault("kart_number", ""));

            var metaObj = new Dictionary<string, object>
            {
                ["track_name"] = trackName,
                ["Track name"] = trackName,
                ["date"] = date,
                ["Date"] = date,
                ["time"] = time,
                ["Time"] = time,
                ["driver_name"] = driverName,
                ["Driver name"] = driverName,
                ["league"] = league,
                ["League"] = league,
                ["class"] = className,
                ["class_name"] = className,
                ["Class"] = className,
                ["session"] = session,
                ["session_name"] = session,
                ["Session"] = session
            };

            if (!string.IsNullOrEmpty(kartNumber))
            {
                metaObj["kart_number"] = kartNumber;
                metaObj["Kart number"] = kartNumber;
            }

            if (!string.IsNullOrEmpty(trackConfig))
            {
                metaObj["track_configuration"] = trackConfig;
                metaObj["Track configuration"] = trackConfig;
                metaObj["track_layout"] = trackConfig;
                metaObj["Track layout"] = trackConfig;
            }

            var channelsList = new List<object>();
            foreach (var ch in channels)
            {
                channelsList.Add(new Dictionary<string, object>
                {
                    ["name"] = ch.Name,
                    ["scale"] = ch.Scale
                });
            }

            var rootObj = new Dictionary<string, object>
            {
                ["metadata"] = metaObj,
                ["channels"] = channelsList
            };

            string jsonStr = JsonSerializer.Serialize(rootObj);
            byte[] jsonBytes = Encoding.UTF8.GetBytes(jsonStr);
            if (jsonBytes.Length > 65535)
            {
                throw new InvalidOperationException("Metadata JSON chunk exceeds 65535 bytes");
            }
            WriteChunk(output, TypeMetadataJson, jsonBytes);
        }

        public static void WriteBaselineChunk(Stream output, double[] values)
        {
            byte[] payload = new byte[values.Length * 8];
            for (int i = 0; i < values.Length; i++)
            {
                BinaryPrimitives.WriteDoubleLittleEndian(payload.AsSpan(i * 8, 8), values[i]);
            }
            WriteChunk(output, TypeBaseline, payload);
        }

        public static void WriteDeltaChunk(Stream output, short[] deltas)
        {
            byte[] payload = new byte[deltas.Length * 2];
            for (int i = 0; i < deltas.Length; i++)
            {
                BinaryPrimitives.WriteInt16LittleEndian(payload.AsSpan(i * 2, 2), deltas[i]);
            }
            WriteChunk(output, TypeDelta, payload);
        }

        public static void WriteEosChunk(Stream output)
        {
            WriteChunk(output, TypeEndOfStream, null);
        }

        public static void ConvertCsvToBrt1Stream(Stream csvStream, Stream brt1Output, int maxSamplesBetweenKeyframes = 3000)
        {
            using (var reader = new StreamReader(csvStream, Encoding.UTF8, detectEncodingFromByteOrderMarks: true, bufferSize: 65536, leaveOpen: true))
            {
                var metadata = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
                List<Brt1Channel> channels = null;

                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    string trimmed = line.Trim();
                    if (string.IsNullOrEmpty(trimmed)) continue;

                    string[] parts = trimmed.Split(new char[] { ',' }, 2);
                    if (parts.Length == 2 && parts[0].Trim().Equals("Time", StringComparison.OrdinalIgnoreCase) && parts[1].Contains(":"))
                    {
                        // Metadata row: Time, HH:mm:ss
                        metadata["Time"] = parts[1].Trim();
                        continue;
                    }

                    // Check if this is the CSV header row (contains Time as the first column followed by channels)
                    if (trimmed.StartsWith("Time,", StringComparison.OrdinalIgnoreCase))
                    {
                        string[] colHeaders = trimmed.Split(',');
                        channels = new List<Brt1Channel>();
                        foreach (string col in colHeaders)
                        {
                            string colName = col.Trim();
                            channels.Add(new Brt1Channel(colName, GetDefaultChannelScale(colName)));
                        }
                        break;
                    }

                    // Otherwise, parse key-value metadata row before column headers
                    if (parts.Length == 2)
                    {
                        string key = parts[0].Trim();
                        string val = parts[1].Trim();
                        metadata[key] = val;
                    }
                }

                if (channels == null || channels.Count == 0)
                {
                    throw new InvalidDataException("Invalid telemetry CSV: missing column header line starting with 'Time,'.");
                }

                // 1. Write Magic chunk
                WriteMagicChunk(brt1Output);

                // 2. Write Metadata JSON chunk
                WriteMetadataChunk(brt1Output, metadata, channels);

                // 3. Process telemetry data rows
                int m = channels.Count;
                bool hasBaseline = false;
                double[] trackedValues = new double[m];
                int samplesSinceKeyframe = 0;

                short[] deltas = new short[m];
                double[] nextTracked = new double[m];

                while ((line = reader.ReadLine()) != null)
                {
                    if (string.IsNullOrWhiteSpace(line)) continue;

                    string[] tokens = line.Split(',');
                    double[] currentValues = new double[m];

                    for (int j = 0; j < m; j++)
                    {
                        if (j < tokens.Length)
                        {
                            string token = tokens[j].Trim();
                            if (j == 0)
                            {
                                // First column is Time. May be ISO-8601 or numeric seconds.
                                if (DateTimeOffset.TryParse(token, CultureInfo.InvariantCulture,
                                                            DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal,
                                                            out DateTimeOffset dto))
                                {
                                    currentValues[0] = dto.ToUnixTimeMilliseconds() / 1000.0;
                                }
                                else if (double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out double d))
                                {
                                    currentValues[0] = d;
                                }
                                else
                                {
                                    currentValues[0] = double.NaN;
                                }
                            }
                            else
                            {
                                if (double.TryParse(token, NumberStyles.Float, CultureInfo.InvariantCulture, out double d))
                                {
                                    currentValues[j] = d;
                                }
                                else
                                {
                                    currentValues[j] = double.NaN;
                                }
                            }
                        }
                        else
                        {
                            currentValues[j] = double.NaN;
                        }
                    }

                    if (!hasBaseline || samplesSinceKeyframe >= maxSamplesBetweenKeyframes)
                    {
                        WriteBaselineChunk(brt1Output, currentValues);
                        Array.Copy(currentValues, trackedValues, m);
                        hasBaseline = true;
                        samplesSinceKeyframe = 0;
                        continue;
                    }

                    // Check for transition from NaN to non-NaN
                    bool needsKeyframe = false;
                    for (int j = 0; j < m; j++)
                    {
                        if (double.IsNaN(trackedValues[j]) && !double.IsNaN(currentValues[j]))
                        {
                            needsKeyframe = true;
                            break;
                        }
                    }

                    if (!needsKeyframe)
                    {
                        for (int j = 0; j < m; j++)
                        {
                            double curr = currentValues[j];
                            if (double.IsNaN(curr))
                            {
                                deltas[j] = NanDelta;
                                nextTracked[j] = double.NaN;
                            }
                            else
                            {
                                double prev = trackedValues[j];
                                double diff = curr - prev;
                                double scale = channels[j].Scale;
                                long q = (long)Math.Round(diff / scale);
                                if (q < -32767 || q > 32767)
                                {
                                    needsKeyframe = true;
                                    break;
                                }
                                deltas[j] = (short)q;
                                nextTracked[j] = prev + ((double)deltas[j] * scale);
                            }
                        }
                    }

                    if (needsKeyframe)
                    {
                        WriteBaselineChunk(brt1Output, currentValues);
                        Array.Copy(currentValues, trackedValues, m);
                        hasBaseline = true;
                        samplesSinceKeyframe = 0;
                    }
                    else
                    {
                        WriteDeltaChunk(brt1Output, deltas);
                        Array.Copy(nextTracked, trackedValues, m);
                        samplesSinceKeyframe++;
                    }
                }

                // 4. Write End of Stream chunk
                WriteEosChunk(brt1Output);
            }
        }
    }
}
