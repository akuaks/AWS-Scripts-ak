#!/usr/bin/python3
import redshift_connector
from redshiftfunc import getiamcredentials
import log
import logging
import re
import argparse
import queries
from datetime import datetime

__version__ = "1.0"
logger = log.setup_custom_logger('UserPrivilegesTransfer')
logger.info('Starting the MetadataTransferUtility - UserPrivileges')

grantdict = {
    'a': 'INSERT',
    'r': 'SELECT',
    'w': 'UPDATE',
    'd': 'DELETE',
    'R': 'RULE',
    'x': 'REFERENCES',
    't': 'TRIGGER',
    'X': 'EXECUTE',
    'U': 'USAGE',
    'C': 'CREATE',
    'T': 'TEMPORARY'
}


def decodeprivs(privs):
        grants = privs
        # Break the aclitem into grantor, grantee and privileges
        privs = re.search(r'=(.*?)/', grants).group(1)
        grantee = re.search(r'^(.*?)=', grants).group(1)
        grantor = re.search(r'/(.*?)$', grants).group(1)
        # Convert empty grantee to group PUBLIC
        if grantee == '':
            grantee = 'PUBLIC'
        decodegrantopt = []
        decodenograntopt = []
        # Take privileges from aclitem and convert to corresponding grant keyword from dictionary
        if privs == 'arwdRxt':
            decodenograntopt.append('ALL')
        elif privs == 'a*r*w*d*R*x*t*':
            decodegrantopt.append('ALL')
        else:
            a = re.compile(r'[arwdRxtXUCT]\*')
            grantoption = [x.replace('*', '') for x in a.findall(privs)]
            nograntoption = list(a.sub('', privs))
            for i in grantoption:
                decodegrantopt.append(grantdict[i])
            for i in nograntoption:
                decodenograntopt.append(grantdict[i])
        # Take grant list from previous step and put elements together in single string
        allgrants = {'decodegrantopt': ', '.join(decodegrantopt), 'decodenograntopt': ', '.join(decodenograntopt),
                     'grantee': grantee, 'grantor': grantor}
        return allgrants


def deriveddls(privlist, targetuser):
    try:
        ddllist = []
        for i in privlist:
            objname = i[2]
            objtype = i[3]
            objowner = i[0]
            if objtype == 'table' or objtype == 'function':
                schemaname = i[1] + '.'
            elif objtype == 'default acl':
                schemaname = i[1]
            else:
                schemaname = ''
            current_user = targetuser
            privileges = i[4]
            if privileges:
                y = privileges.split(',')
                for j in y:
                    privs = decodeprivs(j)
                    grantor = privs['grantor']
                    grantee = privs['grantee']
                    if not grantor.isdigit() and not grantee.isdigit():
                        grantoption = privs['decodegrantopt']
                        nograntoption = privs['decodenograntopt']

                        if grantor != current_user and grantor != 'rdsdb' and objtype != 'default acl':
                            ddl = 'SET SESSION AUTHORIZATION ' + grantor + ';'
                            ddllist.append(ddl)

                        if grantoption and grantee != 'rdsdb':
                            if objtype == 'default acl' and schemaname:
                                ddl = 'ALTER DEFAULT PRIVILEGES FOR USER %s IN SCHEMA %s GRANT %s on %s to %s ' \
                                      'WITH GRANT OPTION;' % (objowner, schemaname, grantoption, objname, grantee)
                                ddllist.append(ddl)
                            elif objtype == 'default acl':
                                ddl = 'ALTER DEFAULT PRIVILEGES FOR USER %s GRANT %s on %s to %s WITH GRANT OPTION;' % \
                                      (objowner, grantoption, objname, grantee)
                                ddllist.append(ddl)
                            else:
                                ddl = 'GRANT %s on %s %s%s to %s WITH GRANT OPTION;' % (grantoption, objtype,
                                                                                        schemaname, objname, grantee)
                                ddllist.append(ddl)
                        if nograntoption and grantee != 'rdsdb':
                            if objtype == 'default acl' and schemaname:
                                ddl = 'ALTER DEFAULT PRIVILEGES FOR USER %s IN SCHEMA %s GRANT %s on %s to %s;' % \
                                      (objowner, schemaname, nograntoption, objname, grantee)
                                ddllist.append(ddl)
                            elif objtype == 'default acl':
                                ddl = 'ALTER DEFAULT PRIVILEGES FOR USER %s GRANT %s on %s to %s;' % \
                                      (objowner, nograntoption, objname, grantee)
                                ddllist.append(ddl)
                            else:
                                ddl = 'GRANT %s on %s %s%s to %s;' % (nograntoption, objtype, schemaname,
                                                                      objname, grantee)
                                ddllist.append(ddl)

                        if grantor != current_user and grantor != 'rdsdb' and objtype != 'default acl':
                            ddl = 'RESET SESSION AUTHORIZATION;'
                            ddllist.append(ddl)
        return ddllist
    except Exception as err:
        logger.error(err)
        exit()
      
        
def executeddls(srccursor, tgtcursor, privquery, tgtuser, ddltype=None):

    srcobjconfig = srccursor.execute(privquery)
    tgtobjconfig = tgtcursor.execute(privquery)

    srcobjconf = srcobjconfig.fetchall()
    srcobjs = ()

    tgtobjconf = tgtobjconfig.fetchall()
    tgtobjs = ()

    for tps in srcobjconf:
        srcobjs = srcobjs + (tuple(tps),)

    for tps in tgtobjconf:
        tgtobjs = tgtobjs + (tuple(tps),)


    if ddltype == 'defacl':

        srcdefaclprivs = srccursor.execute(privquery)
        tgtschemalist = tgtcursor.execute(queries.schemalist)
        tgtobjprivs = [x for x in srcdefaclprivs if (x[1]) in set((y[0]) for y in tgtschemalist) or x[2] is None]
    
    else:
        objintersect = [x for x in srcobjs if (x[1], x[2], x[3]) in set((y[1], y[2], y[3]) for y in tgtobjs)
                        and x[4] is not None]
        # Find common objects with missing grants
        tgtobjprivs = list(set(objintersect) - set(tgtobjs))
    
    objddl = deriveddls(tgtobjprivs, tgtuser)
    

    if objddl:
        try:
            for i in objddl:
                tgtcursor.execute(i)
                logger.info(i)
        except Exception as err:
            logger.error(err)
            exit()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tgtcluster", help="<target cluster endpoint>")
    parser.add_argument("--tgtuser", help="<superuser on target cluster>")
    parser.add_argument("--srccluster", help="<source cluster endpoint>")
    parser.add_argument("--srcuser", help="<superuser on source cluster>")
    parser.add_argument("--srcdbname", help="<source cluster database>")
    parser.add_argument("--tgtdbname", help="<target cluster database>")
    parser.add_argument("--dbport", help="set database port", default=5439)
    args = parser.parse_args()

    srchost = args.srccluster
    srclusterid = srchost.split('.')[0]
    srcuser = args.srcuser
    srcdbname = args.srcdbname
    tgtdbname = args.tgtdbname
    tgtuser = args.tgtuser
    tgthost = args.tgtcluster
    tgtclusterid = tgthost.split('.')[0]
    rsport = args.dbport

    logger.info("Starting UserPrivileges migration")


    if srchost is None or tgthost is None or srcuser is None or tgtuser is None:
        parser.print_help()
        exit()
    
    # Get IAM Credentials
    src_credentials = getiamcredentials(srchost,srcdbname,srcuser)
    tgt_credentials = getiamcredentials(tgthost,tgtdbname,tgtuser)

    # Extract temp credentials
    src_rs_user=src_credentials['DbUser']
    src_rs_pwd =src_credentials['DbPassword']

    tgt_rs_user=tgt_credentials['DbUser']
    tgt_rs_pwd =tgt_credentials['DbPassword']

    try:
        src_rs_conn = redshift_connector.connect(database=srcdbname, user=src_rs_user, password=src_rs_pwd, host=srchost, port=rsport, ssl=True)
        src_rs_conn.autocommit = True

        logger.info("Successfully connected to Redshift cluster: %s" % srchost)
        srccur: redshift_connector.Cursor = src_rs_conn.cursor()


        tgt_rs_conn = redshift_connector.connect(database=tgtdbname, user=tgt_rs_user, password=tgt_rs_pwd, host=tgthost, port=rsport, ssl=True)
        tgt_rs_conn.autocommit = True
        logger.info("Successfully connected to Redshift cluster: %s" % tgthost)
        tgtcur: redshift_connector.Cursor = tgt_rs_conn.cursor()

        #Set the Application Name
        set_name = "set application_name to 'MetadataTransferUtility-v%s'" % __version__

        srccur.execute(set_name)
        tgtcur.execute(set_name)
    
        # Transfer privileges
        logger.info("Executing language privileges")
        executeddls(srccur, tgtcur, queries.languageprivs, tgtuser)
        logger.info("Executing database privileges")
        executeddls(srccur, tgtcur, queries.databaseprivs, tgtuser)
        logger.info("Executing schema privileges")
        executeddls(srccur, tgtcur, queries.schemaprivs, tgtuser)
        logger.info("Executing table privileges")
        executeddls(srccur, tgtcur, queries.tableprivs, tgtuser)
        logger.info("Executing function privileges")
        executeddls(srccur, tgtcur, queries.functionprivs, tgtuser)
        logger.info("Executing ACL privileges")
        executeddls(srccur, tgtcur, queries.defaclprivs, tgtuser, 'defacl')

    # Commit all transactions and cleaup cursor and connection objects
    except Exception as err:
        logger.error(err)
        exit()


if __name__ == "__main__":
    main()
