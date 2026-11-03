// gencert.cs - generate a local CA + leaf cert for mp.weixin.qq.com, export as PEM
// build: csc /target:exe /out:gencert.exe gencert.cs /r:System.dll /r:System.Core.dll
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Security.Cryptography.X509Certificates;
using System.Text;

static class GenCert
{
    const string HOST = "mp.weixin.qq.com";

    static byte[] Tlv(byte tag, byte[] content)
    {
        List<byte> b = new List<byte>();
        b.Add(tag);
        int len = content.Length;
        if (len < 0x80) b.Add((byte)len);
        else if (len <= 0xFF) { b.Add(0x81); b.Add((byte)len); }
        else { b.Add(0x82); b.Add((byte)(len >> 8)); b.Add((byte)(len & 0xFF)); }
        b.AddRange(content);
        return b.ToArray();
    }

    static byte[] Seq(params byte[][] items)
    {
        List<byte> all = new List<byte>();
        foreach (byte[] it in items) all.AddRange(it);
        return Tlv(0x30, all.ToArray());
    }

    static byte[] Int(byte[] v)
    {
        int i = 0;
        while (i < v.Length - 1 && v[i] == 0) i++;
        List<byte> val = new List<byte>();
        for (int k = i; k < v.Length; k++) val.Add(v[k]);
        if (val.Count == 0 || (val[0] & 0x80) != 0) val.Insert(0, 0);
        return Tlv(0x02, val.ToArray());
    }

    static byte[] RsaPrivateKeyDer(RSA rsa)
    {
        RSAParameters p = rsa.ExportParameters(true);
        return Seq(
            Int(new byte[] { 0 }),
            Int(p.Modulus), Int(p.Exponent), Int(p.D), Int(p.P), Int(p.Q),
            Int(p.DP), Int(p.DQ), Int(p.InverseQ));
    }

    static string Pem(string label, byte[] der)
    {
        string b64 = Convert.ToBase64String(der);
        StringBuilder sb = new StringBuilder();
        sb.Append("-----BEGIN ").Append(label).Append("-----\n");
        for (int i = 0; i < b64.Length; i += 64)
            sb.Append(b64.Substring(i, Math.Min(64, b64.Length - i))).Append("\n");
        sb.Append("-----END ").Append(label).Append("-----\n");
        return sb.ToString();
    }

    static void Main(string[] args)
    {
        string dir = args.Length > 0 ? args[0] : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "certs");
        Directory.CreateDirectory(dir);

        RSA caKey = new RSACryptoServiceProvider(2048);
        CertificateRequest caReq = new CertificateRequest(
            "CN=WxCap Local CA", caKey, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        caReq.CertificateExtensions.Add(new X509BasicConstraintsExtension(true, false, 0, true));
        caReq.CertificateExtensions.Add(new X509KeyUsageExtension(
            X509KeyUsageFlags.KeyCertSign | X509KeyUsageFlags.CrlSign, true));
        caReq.CertificateExtensions.Add(new X509SubjectKeyIdentifierExtension(caReq.PublicKey, false));
        X509Certificate2 ca = caReq.CreateSelfSigned(
            DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddYears(10));

        RSA leafKey = new RSACryptoServiceProvider(2048);
        CertificateRequest leafReq = new CertificateRequest(
            "CN=" + HOST, leafKey, HashAlgorithmName.SHA256, RSASignaturePadding.Pkcs1);
        leafReq.CertificateExtensions.Add(new X509BasicConstraintsExtension(false, false, 0, false));
        leafReq.CertificateExtensions.Add(new X509KeyUsageExtension(
            X509KeyUsageFlags.DigitalSignature | X509KeyUsageFlags.KeyEncipherment, false));
        leafReq.CertificateExtensions.Add(new X509EnhancedKeyUsageExtension(
            new OidCollection { new Oid("1.3.6.1.5.5.7.3.1") }, false));
        SubjectAlternativeNameBuilder san = new SubjectAlternativeNameBuilder();
        san.AddDnsName(HOST);
        leafReq.CertificateExtensions.Add(san.Build());

        X509Certificate2 leaf = leafReq.Create(ca,
            DateTimeOffset.UtcNow.AddDays(-1), DateTimeOffset.UtcNow.AddYears(2),
            new byte[] { 0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x08 });

        File.WriteAllText(Path.Combine(dir, "ca.crt"), Pem("CERTIFICATE", ca.RawData), new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(dir, "server.crt"),
            Pem("CERTIFICATE", leaf.RawData) + Pem("CERTIFICATE", ca.RawData), new UTF8Encoding(false));
        File.WriteAllText(Path.Combine(dir, "server.key"),
            Pem("RSA PRIVATE KEY", RsaPrivateKeyDer(leafKey)), new UTF8Encoding(false));

        Console.WriteLine("written to " + dir);
        Console.WriteLine("ca thumbprint: " + ca.Thumbprint);
    }
}
